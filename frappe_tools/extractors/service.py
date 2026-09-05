"""Reusable document-extraction staging and queueing service.

This is the backend entry point for code-owned adapters.  Interactive APIs may
delegate here, but path ingestion deliberately remains a Python-only operation
so a whitelisted endpoint can never read arbitrary server files.
"""

import base64
import hashlib
import io
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.utils import now_datetime

from frappe_tools.extractors import get_plugin, has_plugin, pipeline
from frappe_tools.extractors.context import ExtractionContext


ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_DOCUMENT_SUFFIXES = ALLOWED_IMAGE_SUFFIXES | {".pdf"}
MAX_PAGES = 20
MAX_PAGE_BYTES = 15 * 1024 * 1024
MAX_RENDER_PIXELS = 20_000_000


def create_extraction(target_doctype=None, *, selected_layout=None, operation_mode="Create New",
	existing_document=None, status="Draft", check_permissions=True):
	"""Create a durable extraction parent for any code-owned adapter."""
	target_doctype = _target_for_layout(selected_layout, target_doctype)
	operation_mode = str(operation_mode or "Create New").strip()
	if operation_mode not in {"Create New", "Attach Existing"}:
		frappe.throw(_("Unknown scanner operation mode {0}.").format(operation_mode))
	if target_doctype:
		_validate_target(target_doctype, check_permissions=check_permissions,
			require_create=operation_mode == "Create New")
		tables = get_plugin(target_doctype).schema(ExtractionContext(target_doctype)).get("tables") or []
	else:
		_validate_auto_intake(check_permissions=check_permissions)
		tables = []
	doc = frappe.new_doc("Document Extraction")
	doc.target_doctype = target_doctype
	doc.selected_layout = selected_layout
	doc.operation_mode = operation_mode
	doc.existing_document = str(existing_document or "").strip() or None
	if operation_mode == "Attach Existing":
		_validate_existing_target(target_doctype, doc.existing_document, check_permissions=check_permissions)
	doc.status = status
	doc.auto_decide = 1
	doc.line_table = tables[0]["table"] if tables else None
	doc.insert(ignore_permissions=not check_permissions)
	return doc


def stage_data_urls(extraction, data_urls):
	"""Persist base64 image pages in the site's private local file store."""
	doc = _extraction_doc(extraction)
	values = list(data_urls or [])
	_validate_page_count(values)
	pages = []
	for page_no, data_url in enumerate(values, 1):
		url, width, height = pipeline.save_page_image(data_url, doc.name, page_no)
		pages.append(_page(url, url.rsplit("/", 1)[-1], page_no, width, height))
	return _replace_pages(doc, pages)


def stage_local_paths(extraction, document_paths):
	"""Copy local images/PDFs into private Frappe Files and attach image pages.

	Remote URLs are intentionally unsupported.  Callers must first download a
	document through an explicitly authorised integration and pass its local path.
	"""
	doc = _extraction_doc(extraction)
	raw_paths = [str(value) for value in (document_paths or [])]
	if any(value.lower().startswith(("http://", "https://", "s3://")) for value in raw_paths):
		frappe.throw(_("Remote document URLs are not accepted by the local-path service."))
	try:
		paths = [Path(value).expanduser().resolve(strict=True) for value in raw_paths]
	except (FileNotFoundError, OSError) as exc:
		frappe.throw(_("Document path is not readable: {0}").format(exc))
	data_urls = []
	for path in paths:
		if not path.is_file():
			frappe.throw(_("Document path is not a file: {0}").format(path))
		if path.suffix.lower() not in ALLOWED_DOCUMENT_SUFFIXES:
			frappe.throw(_("Unsupported page type {0}.").format(path.suffix or "unknown"))
		content = path.read_bytes()
		if len(content) > MAX_PAGE_BYTES:
			frappe.throw(_("Each source document must be 15 MB or smaller."))
		for page in document_pages(content, path.suffix, path.name, max_pages=MAX_PAGES - len(data_urls)):
			if len(data_urls) >= MAX_PAGES:
				frappe.throw(_("A maximum of {0} rendered pages is supported per extraction.").format(MAX_PAGES))
			mime = mimetypes.guess_type(f"page{page['suffix']}")[0] or "image/png"
			data_urls.append(f"data:{mime};base64,{base64.b64encode(page['content']).decode()}")
	return stage_data_urls(doc, data_urls)


def enqueue_document_paths(target_doctype, document_paths, *, selected_layout=None, enqueue_after_commit=True, check_permissions=True):
	"""One-call reusable API: create, copy local pages, and enqueue extraction."""
	doc = create_extraction(target_doctype, selected_layout=selected_layout, check_permissions=check_permissions)
	stage_local_paths(doc, document_paths)
	return enqueue(doc, enqueue_after_commit=enqueue_after_commit)


def create_from_file_urls(target_doctype, file_urls, *, selected_layout=None, operation_mode="Create New",
	existing_document=None, auto_process=True, check_permissions=True):
	"""Create one durable run from already-uploaded local Frappe File URLs.

	This is the reusable backend capture API for LR, Purchase Invoice and future
	code-owned adapters. Original files remain attached as evidence; PDFs are
	rendered into page images only inside the preparation job.
	"""
	target_doctype = _target_for_layout(selected_layout, target_doctype)
	operation_mode = str(operation_mode or "Create New").strip()
	if operation_mode not in {"Create New", "Attach Existing"}:
		frappe.throw(_("Unknown scanner operation mode {0}.").format(operation_mode))
	if target_doctype:
		_validate_target(target_doctype, check_permissions=check_permissions,
			require_create=operation_mode == "Create New")
	else:
		_validate_auto_intake(check_permissions=check_permissions)
	urls = list(dict.fromkeys(str(value or "").strip() for value in (file_urls or []) if str(value or "").strip()))
	_validate_page_count(urls)
	doc = frappe.new_doc("Document Extraction")
	doc.target_doctype = target_doctype
	doc.selected_layout = selected_layout
	doc.operation_mode = operation_mode
	doc.existing_document = str(existing_document or "").strip() or None
	if operation_mode == "Attach Existing":
		_validate_existing_target(target_doctype, doc.existing_document, check_permissions=check_permissions)
	doc.status = "Draft"
	doc.auto_process = 1 if auto_process else 0
	doc.auto_decide = 1
	tables = get_plugin(target_doctype).schema(ExtractionContext(target_doctype)).get("tables") if target_doctype else []
	doc.line_table = tables[0]["table"] if tables else None
	for file_url in urls:
		_validate_local_file_url(file_url, check_permissions=check_permissions)
		doc.append("source_files", {"source_file": file_url, "status": "Pending"})
	doc.insert(ignore_permissions=not check_permissions)
	return doc


def create_from_scanned_document_details(target_doctype, detail_names, *, selected_layout=None, auto_process=True, check_permissions=True):
	"""Start a new run from existing scan records without copying or re-uploading files."""
	names = list(dict.fromkeys(str(value or "").strip() for value in (detail_names or []) if str(value or "").strip()))
	_validate_page_count(names)
	if not selected_layout and names:
		parents = set(frappe.get_all("Scanned Document Detail", filters={"name": ["in", names]}, pluck="scanner_document"))
		parents.discard(None)
		if len(parents) == 1:
			selected_layout = frappe.db.get_value("Scanned Document", next(iter(parents)), "scanner_layout")
	target_doctype = _target_for_layout(selected_layout, target_doctype)
	if target_doctype:
		_validate_target(target_doctype, check_permissions=check_permissions)
	else:
		_validate_auto_intake(check_permissions=check_permissions)
	doc = frappe.new_doc("Document Extraction")
	doc.target_doctype = target_doctype
	doc.selected_layout = selected_layout
	doc.status = "Draft"
	doc.auto_process = 1 if auto_process else 0
	doc.auto_decide = 1
	tables = get_plugin(target_doctype).schema(ExtractionContext(target_doctype)).get("tables") if target_doctype else []
	doc.line_table = tables[0]["table"] if tables else None
	for name in names:
		detail = frappe.get_doc("Scanned Document Detail", name)
		if check_permissions:
			detail.check_permission("read")
		if detail.is_deleted or not detail.attachment:
			frappe.throw(_("Scanned Document Detail {0} has no active attachment.").format(name))
		_validate_local_file_url(detail.attachment, check_permissions=check_permissions)
		doc.append("source_files", {
			"source_file": detail.attachment,
			"scanned_document_detail": detail.name,
			"status": "Pending",
		})
	doc.insert(ignore_permissions=not check_permissions)
	return doc


def enqueue_attached_sources(extraction, *, enqueue_after_commit=True, check_permissions=True):
	"""Schedule local source preparation exactly once for a Draft/Failed run."""
	doc = _extraction_doc(extraction)
	if check_permissions:
		doc.check_permission("write")
	if doc.target_doctype:
		_validate_target(doc.target_doctype, check_permissions=check_permissions)
	else:
		_validate_auto_intake(check_permissions=check_permissions)
	if doc.status not in {"Draft", "Failed"}:
		frappe.throw(_("Only a Draft or Failed processing run can be started."))
	if not any(row.source_file for row in doc.source_files):
		frappe.throw(_("Attach at least one source image or PDF."))
	job_id = f"document-source-preparation::{doc.name}"
	frappe.db.set_value("Document Extraction", doc.name, {
		"status": "Preparing",
		"processing_job_id": job_id,
		"processing_started_on": now_datetime(),
		"processing_completed_on": None,
		"error_log": None,
	}, update_modified=False)
	frappe.enqueue(
		"frappe_tools.extractors.service.prepare_attached_sources",
		queue="short",
		timeout=300,
		extraction_name=doc.name,
		enqueue_after_commit=enqueue_after_commit,
		job_id=job_id,
		deduplicate=True,
	)
	return {"extraction": doc.name, "status": "Preparing", "job_id": job_id}


def prepare_attached_sources(extraction_name):
	"""Render the run's local images/PDFs and hand it to the vision queue."""
	doc = frappe.get_doc("Document Extraction", extraction_name)
	if doc.status not in {"Preparing", "Draft"}:
		return {"extraction": doc.name, "status": doc.status}
	try:
		doc.flags.skip_auto_process = True
		doc.add_processing_event("Source Preparation", "Preparing", "Reading locally attached source files.")
		page_rows = []
		for source_index, source in enumerate(doc.source_files, 1):
			source.status = "Preparing"
			source.error_message = None
			file_doc, content, suffix = _attached_file_content(source.source_file)
			remaining = MAX_PAGES - len(page_rows)
			if remaining <= 0:
				frappe.throw(_("A maximum of {0} rendered pages is supported per extraction.").format(MAX_PAGES))
			pages = document_pages(content, suffix, file_doc.file_name, max_pages=remaining)
			first_page = len(page_rows) + 1
			for source_page_no, page in enumerate(pages, 1):
				mime = mimetypes.guess_type(f"page{page['suffix']}")[0] or "image/png"
				data_url = f"data:{mime};base64,{base64.b64encode(page['content']).decode()}"
				page_no = len(page_rows) + 1
				url, width, height = pipeline.save_page_image(data_url, doc.name, page_no)
				page_rows.append({
					**_page(url, url.rsplit("/", 1)[-1], page_no, width, height),
					"source_row": source.name,
					"source_file": source.source_file,
					"scanned_document_detail": source.scanned_document_detail,
					"source_page_no": source_page_no,
				})
			source.source_type = "PDF" if suffix == ".pdf" else "Image"
			source.original_file_name = file_doc.file_name
			source.content_type = mimetypes.guess_type(file_doc.file_name or "")[0] or "application/octet-stream"
			source.file_size = len(content)
			source.content_hash = hashlib.sha256(content).hexdigest()
			source.page_from = first_page
			source.page_to = len(page_rows)
			source.status = "Ready"
		doc.set("pages", [])
		for page in page_rows:
			doc.append("pages", page)
		doc.source_count = len(doc.source_files)
		doc.page_count = len(page_rows)
		doc.add_processing_event("Source Preparation", "Ready", f"Prepared {len(doc.source_files)} source file(s) into {len(page_rows)} page(s).")
		doc.save(ignore_permissions=True)
		return enqueue(doc, enqueue_after_commit=False)
	except Exception as exc:
		frappe.db.rollback()
		failed = frappe.get_doc("Document Extraction", extraction_name)
		failed.flags.skip_auto_process = True
		failed.status = "Failed"
		failed.processing_completed_on = now_datetime()
		failed.error_log = f"{exc}\n\n{frappe.get_traceback()}"[:14000]
		for source in failed.source_files:
			if source.status != "Ready":
				source.status = "Failed"
				source.error_message = str(exc)[:500]
		failed.add_processing_event("Source Preparation", "Failed", str(exc)[:500])
		failed.save(ignore_permissions=True)
		frappe.db.commit()
		return {"extraction": failed.name, "status": "Failed", "error": str(exc)}


def enqueue(extraction, *, enqueue_after_commit=True):
	"""Queue classification or target extraction exactly once."""
	doc = _extraction_doc(extraction)
	page_count = len([page for page in doc.pages if page.image])
	if not page_count:
		frappe.throw(_("No scanned pages were provided."))
	if not doc.target_doctype:
		from frappe_tools.extractors import classification

		return classification.enqueue(doc, enqueue_after_commit=enqueue_after_commit)
	doc.status = "Queued"
	doc.error_log = None
	doc.add_processing_event("Extraction", "Queued", f"Queued {page_count} rendered page(s) for vision extraction.")
	doc.flags.skip_auto_process = True
	doc.save()
	job = pipeline.enqueue_extraction(doc.name, page_count, enqueue_after_commit=enqueue_after_commit)
	frappe.db.set_value("Document Extraction", doc.name, "processing_job_id", job["job_id"], update_modified=False)
	return {"extraction": doc.name, "status": doc.status, **job}


def _validate_target(target_doctype, *, check_permissions, require_create=True):
	if not frappe.db.exists("DocType", target_doctype):
		frappe.throw(_("Unknown DocType {0}.").format(target_doctype))
	if check_permissions:
		frappe.has_permission(target_doctype, "read", throw=True)
		if require_create:
			frappe.has_permission(target_doctype, "create", throw=True)
	if not has_plugin(target_doctype):
		frappe.throw(_("No code-owned document adapter exists for {0}.").format(target_doctype))


def _validate_existing_target(target_doctype, existing_document, *, check_permissions):
	if not existing_document or not frappe.db.exists(target_doctype, existing_document):
		frappe.throw(_("Select an existing {0} document to attach this scan.").format(target_doctype))
	if check_permissions and not frappe.has_permission(target_doctype, doc=existing_document, ptype="write"):
		frappe.throw(_("You do not have permission to attach scans to {0} {1}.").format(
			target_doctype, existing_document), frappe.PermissionError)


def _target_for_layout(selected_layout, target_doctype=None):
	"""Validate the intake layout and return its authoritative target DocType."""
	selected_layout = str(selected_layout or "").strip() or None
	target_doctype = str(target_doctype or "").strip() or None
	if not selected_layout:
		return target_doctype
	layout_target = frappe.db.get_value("Document Scanner Layout", selected_layout, "layout_doctype")
	if not layout_target:
		frappe.throw(_("Document Scanner Layout {0} does not exist.").format(selected_layout))
	if target_doctype and target_doctype != layout_target:
		frappe.throw(
			_("Layout {0} belongs to {1}, not {2}.").format(selected_layout, layout_target, target_doctype)
		)
	return layout_target


def _validate_auto_intake(*, check_permissions):
	from frappe_tools.extractors.classification import candidate_profiles

	if not candidate_profiles(check_permissions=check_permissions):
		frappe.throw(_("No code-owned document adapter is available for automatic classification."))


def _extraction_doc(extraction):
	return extraction if getattr(extraction, "doctype", None) == "Document Extraction" else frappe.get_doc("Document Extraction", extraction)


def _validate_page_count(values):
	if not values:
		frappe.throw(_("No scanned pages were provided."))
	if len(values) > MAX_PAGES:
		frappe.throw(_("A maximum of {0} pages is supported per extraction.").format(MAX_PAGES))


def _validate_local_file_url(file_url, *, check_permissions=True):
	file_doc, _, _ = _attached_file_content(file_url, read_content=False)
	if check_permissions:
		file_doc.check_permission("read")
	return file_doc


def _attached_file_content(file_url, *, read_content=True):
	"""Resolve only site-local File records without invoking a remote storage hook."""
	value = str(file_url or "").strip()
	parsed = urlparse(value)
	if parsed.scheme or value.startswith("//") or value.startswith("/api/") or "frappe_s3_integration" in value:
		frappe.throw(_("Remote or proxy-backed source files are not accepted. Upload a local private file first."))
	file_name = frappe.db.get_value("File", {"file_url": value}, "name")
	if not file_name:
		frappe.throw(_("Source file {0} is not a local Frappe File.").format(value))
	file_doc = frappe.get_doc("File", file_name)
	suffix = Path(file_doc.file_name or parsed.path).suffix.lower()
	if suffix not in ALLOWED_DOCUMENT_SUFFIXES:
		frappe.throw(_("Unsupported source type {0}; this adapter accepts images and PDFs.").format(suffix or "unknown"))
	if not read_content:
		return file_doc, None, suffix
	path = Path(file_doc.get_full_path()).resolve(strict=True)
	content = path.read_bytes()
	if len(content) > MAX_PAGE_BYTES:
		frappe.throw(_("Each source document must be 15 MB or smaller."))
	return file_doc, content, suffix


def _page(url, file_name, page_no, width, height):
	return {"page_no": page_no, "image": url, "file_name": file_name, "width": width, "height": height}


def document_pages(content, suffix, filename="document", max_pages=MAX_PAGES):
	"""Return bounded image pages for one local source document."""
	suffix = str(suffix or "").lower()
	if suffix in ALLOWED_IMAGE_SUFFIXES:
		width, height = _validate_image(content, filename)
		return [{"content": content, "suffix": suffix, "width": width, "height": height}]
	if suffix != ".pdf":
		frappe.throw(_("Unsupported page type {0}.").format(suffix or "unknown"))
	try:
		import fitz
	except ImportError:
		frappe.throw(_("PDF capture requires PyMuPDF on the Frappe worker."))
	try:
		pdf = fitz.open(stream=content, filetype="pdf")
	except Exception:
		frappe.throw(_("{0} is not a readable PDF.").format(filename))
	try:
		if pdf.needs_pass:
			frappe.throw(_("Encrypted PDFs are not supported. Upload an unlocked copy."))
		if not pdf.page_count:
			frappe.throw(_("{0} has no pages.").format(filename))
		page_limit = min(max(int(max_pages or 0), 0), MAX_PAGES)
		if pdf.page_count > page_limit:
			frappe.throw(_("This document exceeds the {0}-page extraction limit.").format(page_limit))
		pages = []
		for source_page in pdf:
			area = max(float(source_page.rect.width * source_page.rect.height), 1)
			zoom = min(2.0, (MAX_RENDER_PIXELS / area) ** 0.5)
			pixmap = source_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
			pages.append({
				"content": pixmap.tobytes("png"),
				"suffix": ".png",
				"width": pixmap.width,
				"height": pixmap.height,
			})
		return pages
	finally:
		pdf.close()


def _validate_image(content, filename):
	try:
		from PIL import Image, ImageOps

		with Image.open(io.BytesIO(content)) as image:
			image.verify()
		with Image.open(io.BytesIO(content)) as image:
			return ImageOps.exif_transpose(image).size
	except Exception:
		frappe.throw(_("{0} is not a readable image.").format(filename))


def _replace_pages(doc, pages):
	doc.set("pages", [])
	for page in pages:
		doc.append("pages", page)
	doc.save()
	return doc

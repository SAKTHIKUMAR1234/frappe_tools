"""Read each File once and store its page data on the File."""

import json
import mimetypes
from base64 import b64encode
from io import BytesIO

import frappe
import pypdfium2
from frappe.utils import add_to_date, cint, get_datetime, now_datetime
from rq.timeouts import JobTimeoutException

from frappe_tools.extractors.pipeline import file_to_data_url
from frappe_tools.file_data.extraction import extract_pages
from frappe_tools.i2a.extract import _read_file

FILE = "File"
MAX_FILE_PAGES = 20
MAX_AUTO_ATTEMPTS = 3
RETRY_AFTER_HOURS = 6
PDF_RENDER_SCALE = 2
FILE_FIELDS = [
	"name", "file_name", "file_url", "content_hash", "ai_page_data", "ai_page_count", "ai_page_model",
	"ai_page_error", "ai_page_error_on", "ai_page_attempts",
]


def file_kind(file: dict) -> str | None:
	"""Return "image", "pdf" or None for a file that cannot be read."""
	mime = mimetypes.guess_type(file.file_name or file.file_url or "")[0] or ""
	if mime == "application/pdf":
		return "pdf"
	return "image" if mime.startswith("image/") else None


def read_file(file_name: str, retry_failed: bool = False, context: dict | None = None) -> dict | None:
	"""Return the stored {"pages": [...]} of a File, reading it first when a read is due.

	Holds no lock during the model call and commits after a read. Returns None when the File stays unread.
	"""
	file = frappe.db.get_value(FILE, file_name, FILE_FIELDS, as_dict=True)
	if not file:
		raise frappe.DoesNotExistError(f"File {file_name} not found")
	if file.ai_page_data:
		return frappe.parse_json(file.ai_page_data)
	if not (retry_failed or is_retry_due(file)):
		return None

	values = _read_values(file, context or {})
	if not _save(file, values) or not values.get("ai_page_data"):
		return None
	return frappe.parse_json(values["ai_page_data"])


def retry_cutoff():
	return add_to_date(now_datetime(), hours=-RETRY_AFTER_HOURS)


def is_retry_due(file: dict) -> bool:
	"""A File with no error, or a failed File under the attempt cap whose last failure is old enough."""
	if not file.ai_page_error:
		return True
	return cint(file.ai_page_attempts) < MAX_AUTO_ATTEMPTS and (
		not file.ai_page_error_on or get_datetime(file.ai_page_error_on) <= retry_cutoff()
	)


def data_values(data: dict, model: str | None) -> dict:
	"""Return the File field values that store `data` and clear any earlier error."""
	return {
		"ai_page_data": json.dumps(data, ensure_ascii=False), "ai_page_count": page_total(data),
		"ai_page_data_on": now_datetime(), "ai_page_model": model, "ai_page_error": None,
		"ai_page_error_on": None, "ai_page_attempts": 0,
	}


def page_total(data: dict) -> int:
	"""Pages in the file, also the pages over MAX_FILE_PAGES that were not read."""
	return len(data["pages"]) + cint(data.get("omitted_pages"))


def page_count(file: dict) -> int:
	"""Pages in a File without a model call."""
	if file.ai_page_data:
		return cint(file.ai_page_count)
	if file_kind(file) != "pdf":
		return 1
	pdf = pypdfium2.PdfDocument(_read_file(file.file_url)[0])
	try:
		return len(pdf)
	finally:
		pdf.close()


def _read_values(file, context):
	try:
		return _copied_values(file) or data_values(*_read_pages(file, context))
	except JobTimeoutException:
		raise
	except Exception as exc:  # One unreadable file must not stop the other files.
		return {"ai_page_error": str(exc)[:1000], "ai_page_error_on": now_datetime(),
			"ai_page_attempts": cint(file.ai_page_attempts) + 1}


def _copied_values(file):
	"""Copy the data of another File with the same content, without a model call."""
	if not file.content_hash:
		return None
	source = frappe.db.get_value(FILE, {
		"name": ["!=", file.name], "content_hash": file.content_hash, "ai_page_data": ["is", "set"],
	}, ["ai_page_data", "ai_page_model"], as_dict=True)
	return data_values(frappe.parse_json(source.ai_page_data), source.ai_page_model) if source else None


def _read_pages(file, context):
	"""Read each page in its own model call."""
	images, total = _page_images(file)
	pages, model = [], None
	for number, image in enumerate(images, 1):
		[page], model = extract_pages([image], **context)
		pages.append({**page, "page_no": number})
	data = {"pages": pages}
	if total > len(images):
		data["omitted_pages"] = total - len(images)
	return data, model


def _page_images(file):
	kind = file_kind(file)
	if kind == "image":
		return [file_to_data_url(file.file_url)], 1
	if kind != "pdf":
		raise ValueError("Only image and PDF files can be read")
	pdf = pypdfium2.PdfDocument(_read_file(file.file_url)[0])
	try:
		return [_pdf_page_image(pdf[index]) for index in range(min(len(pdf), MAX_FILE_PAGES))], len(pdf)
	finally:
		pdf.close()


def _pdf_page_image(page):
	buffer = BytesIO()
	page.render(scale=PDF_RENDER_SCALE).to_pil().convert("RGB").save(buffer, "JPEG", quality=85)
	return "data:image/jpeg;base64," + b64encode(buffer.getvalue()).decode()


def _save(file, values):
	"""Write under a short File row lock when the File is unchanged and still unread."""
	current = frappe.db.get_value(FILE, file.name, ["file_url", "content_hash", "ai_page_data"],
		as_dict=True, for_update=True)
	unchanged = bool(current) and not current.ai_page_data and (
		(current.file_url, current.content_hash) == (file.file_url, file.content_hash)
	)
	if unchanged:
		frappe.db.set_value(FILE, file.name, values, update_modified=False)
	frappe.db.commit()
	return unchanged

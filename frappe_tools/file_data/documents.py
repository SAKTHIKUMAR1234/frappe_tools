"""Ordered pages of a document from the page data stored on its Files."""

import frappe

from frappe_tools.file_data.reader import FILE, FILE_FIELDS, file_kind, read_file

SCAN = "Scanned Document"
DETAIL = "Scanned Document Detail"


def get_document_pages(doctype: str, name: str) -> list[dict]:
	"""Return all ordered pages of a document.

	A Scanned Document uses the files of its active pages; any other document uses its attached
	images and PDFs. Unread files are read now. Needs read permission on the document.
	"""
	context = _read_context(doctype, name)
	pages, errors = [], []
	for file, extra in _document_files(doctype, name):
		data = read_file(file.name, retry_failed=True, context=context)
		if data is None:
			errors.append(f"{file.file_name or file.name}: {_error(file.name)}")
			continue
		pages.extend({**page, **extra} for page in data["pages"])
	if errors:
		raise ValueError("Some files could not be read. " + "; ".join(errors))
	return [{**page, "page_no": number} for number, page in enumerate(pages, 1)]


def files_for_document(doctype: str, name: str) -> list[str]:
	"""Return the File names that hold the pages of a document, in page order."""
	return [file.name for file, _extra in _document_files(doctype, name)]


def scan_files(scanned_document: str) -> list[tuple[dict, dict | None]]:
	"""Return (detail, File or None) for each active page of a scan, in page order."""
	details = frappe.get_all(DETAIL, filters={"scanner_document": scanned_document, "is_deleted": 0},
		fields=["name", "attachment", "title"], order_by="page_no asc, name asc")
	files = files_by_url([detail.attachment for detail in details if detail.attachment])
	return [(detail, files.get(detail.attachment)) for detail in details]


def files_by_url(urls: list[str]) -> dict[str, dict]:
	"""Return one File for each url: a read File first, then a scan-owned File, then the oldest."""
	if not urls:
		return {}
	rows = frappe.get_all(FILE, filters={"file_url": ["in", list(set(urls))]},
		fields=[*FILE_FIELDS, "attached_to_doctype"], order_by="creation asc, name asc")
	files = {}
	for row in sorted(rows, key=lambda row: (not row.ai_page_data, row.attached_to_doctype not in (DETAIL, SCAN))):
		files.setdefault(row.file_url, row)
	return files


def _document_files(doctype, name):
	if doctype == SCAN:
		pairs = scan_files(name)
		missing = [detail.name for detail, file in pairs if not file]
		if missing:
			raise ValueError(f"Scan pages have no File: {', '.join(missing)}")
		return [(file, {"section": detail.title}) for detail, file in pairs]
	files = frappe.get_all(FILE, filters={"attached_to_doctype": doctype, "attached_to_name": name, "is_folder": 0},
		fields=FILE_FIELDS, order_by="attached_to_field asc, creation asc, name asc")
	return [(file, {}) for file in files if file_kind(file)]


def _read_context(doctype, name):
	if doctype == SCAN:
		scan = frappe.db.get_value(SCAN, name, ["_doctype", "_docname"], as_dict=True)
		if not scan:
			raise frappe.DoesNotExistError(f"Scanned Document {name} not found")
		frappe.has_permission(scan._doctype, "read", doc=scan._docname, throw=True)
		return dict(target_doctype=scan._doctype, reference_doctype=SCAN, reference_name=name)
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	return dict(target_doctype=doctype, reference_doctype=doctype, reference_name=name)


def _error(file_name):
	return frappe.db.get_value(FILE, file_name, "ai_page_error") or "changed while reading"

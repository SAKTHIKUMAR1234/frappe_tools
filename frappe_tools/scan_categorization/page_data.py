"""Hourly reading of scanned pages; the page data lives on each page's File."""

import frappe
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count, IfNull, Min
from frappe.utils import cint
from frappe.utils.background_jobs import is_job_enqueued
from pypika.terms import ExistsCriterion

from frappe_tools.file_data.documents import DETAIL, SCAN, files_by_url, scan_files
from frappe_tools.file_data.reader import FILE, MAX_AUTO_ATTEMPTS, read_file, retry_cutoff

SETTINGS = "Document Scanner Settings"
METHOD = "frappe_tools.scan_categorization.page_data.extract_scan_pages"
SCAN_FIELDS = ("_doctype", "_docname", "scanner_layout", "ai_run_id", "ai_check_status", "ai_reviewed")
DETAIL_FIELDS = (
	"name", "attachment", "original_attachment", "page_no", "page_type", "title",
	"layout_type", "is_deleted", "scanner_document",
)


def stored_pages(details: list[dict]) -> list[dict] | None:
	"""Return stored page data in detail order (page_no is the 1-based index), or None if a page is unread."""
	files = files_by_url([detail.attachment for detail in details if detail.attachment])
	pages = []
	for index, detail in enumerate(details, 1):
		file = files.get(detail.attachment)
		if not file or not file.ai_page_data:
			return None
		data = frappe.parse_json(file.ai_page_data)
		if len(data["pages"]) != 1:
			raise ValueError(f"Scan page {detail.name} must hold exactly one page")
		pages.append({**data["pages"][0], "page_no": index})
	return pages


def _file_with(detail, condition):
	file = DocType(FILE)
	return ExistsCriterion(frappe.qb.from_(file).select(file.name).where(
		(file.file_url == detail.attachment) & condition(file)
	))


def _has_data(file):
	return file.ai_page_data.isnotnull()


def _is_blocked(file):
	"""Matches a File that `is_retry_due` rejects."""
	return (IfNull(file.ai_page_error, "") != "") & (
		(IfNull(file.ai_page_attempts, 0) >= MAX_AUTO_ATTEMPTS)
		| (IfNull(file.ai_page_error_on, "1900-01-01") > retry_cutoff())
	)


def has_unread_pages(scanned_document: str) -> bool:
	detail = DocType(DETAIL)
	return bool(frappe.qb.from_(detail).select(detail.name).where(
		(detail.scanner_document == scanned_document) & (detail.is_deleted == 0)
		& _file_with(detail, _has_data).negate()
	).limit(1).run())


def enqueue_page_extraction() -> int:
	"""Hourly: queue one job for each scan with unread pages until the page limit is reached."""
	settings = frappe.get_single(SETTINGS)
	if not settings.enable_page_extraction:
		return 0
	limit = cint(settings.page_extraction_batch_limit) or 500
	if limit < 1:
		raise ValueError("Page extraction batch limit must be positive")

	queued = pages = 0
	for name, count in _scans_with_unread_pages(settings.ai_process_since):
		if pages >= limit:
			break
		pages += count
		queued += queue_scan_pages(name)
	return queued


def _scans_with_unread_pages(since):
	"""Return (scan, unread page count) pairs, oldest scan first, for pages whose File read is due."""
	detail, scan = DocType(DETAIL), DocType(SCAN)
	query = (
		frappe.qb.from_(detail).join(scan).on(scan.name == detail.scanner_document)
		.select(detail.scanner_document, Count("*"))
		.where((detail.is_deleted == 0) & _file_with(detail, lambda file: file.name.isnotnull()))
		.where(_file_with(detail, _has_data).negate() & _file_with(detail, _is_blocked).negate())
		.groupby(detail.scanner_document)
		.orderby(Min(scan.creation)).orderby(detail.scanner_document)
	)
	if since:
		query = query.where(scan.creation >= since)
	return query.run()


def queue_scan_pages(name: str, *, retry_failed: bool = False) -> bool:
	job_id = f"scan-page-extraction::{name}"
	if is_job_enqueued(job_id):
		return False
	frappe.enqueue(METHOD, queue="long", timeout=21600, job_id=job_id, name=name, retry_failed=retry_failed)
	return True


def extract_scan_pages(name: str, retry_failed: bool = False) -> None:
	"""Read the unread Files of a scan's active pages."""
	scan = frappe.db.get_value(SCAN, name, ["name", "_doctype"], as_dict=True)
	if not scan:
		return
	context = dict(target_doctype=scan._doctype, reference_doctype=SCAN, reference_name=name,
		run_id=frappe.generate_hash(length=16))
	for _detail, file in scan_files(name):
		if file:
			read_file(file.name, retry_failed=retry_failed, context=context)


def lock_page_snapshot(scan: dict, details: list[dict]) -> tuple[dict | None, str]:
	"""Lock the scan and its active pages; return the current scan and a change reason."""
	current = frappe.db.get_value(SCAN, scan.name, "*", as_dict=True, for_update=True)
	if not current:
		return None, "Scan was removed during AI processing."
	if any(current.get(key) != scan.get(key) for key in SCAN_FIELDS):
		return current, "Scan settings or status changed during AI processing."
	rows = frappe.db.get_values(
		DETAIL, {"scanner_document": scan.name, "is_deleted": 0},
		list(DETAIL_FIELDS), as_dict=True, order_by="page_no asc, name asc", for_update=True,
	)
	if [tuple(row.get(key) for key in DETAIL_FIELDS) for row in rows] != [
		tuple(row.get(key) for key in DETAIL_FIELDS) for row in details
	]:
		return current, "Scan pages changed during AI processing."
	return current, ""

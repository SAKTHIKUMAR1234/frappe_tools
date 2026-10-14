"""Move page data from Scanned Document Detail rows onto their Files."""

import json

import frappe
from frappe.query_builder import DocType

from frappe_tools.file_data.fields import create_file_fields

DETAIL = "Scanned Document Detail"


def execute() -> None:
	create_file_fields()
	if not frappe.db.has_column(DETAIL, "ai_page_data"):
		return
	for detail in _read_details():
		file = _detail_file(detail)
		if file:
			frappe.db.set_value("File", file, _values(detail), update_modified=False)


def _read_details():
	detail = DocType(DETAIL)
	return frappe.qb.from_(detail).select(
		detail.name, detail.attachment, detail.ai_page_data, detail.ai_page_data_on,
	).where(
		detail.ai_page_data.isnotnull() & (detail.ai_page_data_attachment == detail.attachment)
	).orderby(detail.creation).run(as_dict=True)


def _detail_file(detail):
	"""Return the unread File of the detail attachment, preferring the File attached to the detail."""
	files = frappe.get_all("File", filters={"file_url": detail.attachment},
		fields=["name", "attached_to_doctype", "attached_to_name", "ai_page_data"], order_by="creation asc, name asc")
	if any(file.ai_page_data for file in files):
		return None
	owned = [file for file in files if (file.attached_to_doctype, file.attached_to_name) == (DETAIL, detail.name)]
	candidates = owned or files
	return candidates[0].name if candidates else None


def _values(detail):
	page = {**frappe.parse_json(detail.ai_page_data), "page_no": 1}
	return {
		"ai_page_data": json.dumps({"pages": [page]}, ensure_ascii=False), "ai_page_count": 1,
		"ai_page_data_on": detail.ai_page_data_on, "ai_page_attempts": 0,
	}

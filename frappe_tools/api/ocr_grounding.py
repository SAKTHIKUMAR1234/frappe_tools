"""Evidence-grounding actions kept separate for independent deployment reload."""

import json

import frappe
from frappe import _
from frappe.utils import cint

from frappe_tools.api.ocr_agent import _as_dict, _get_extraction, _json_object, get_run
from frappe_tools.extractors import get_plugin, grounding, pipeline
from frappe_tools.extractors.context import ExtractionContext


@frappe.whitelist()
def refresh_grounding(extraction):
	"""Replace unverified model coordinates with local OCR word boxes."""
	doc = _get_extraction(extraction, "write")
	if doc.status not in {"Review", "Created"}:
		frappe.throw(_("Highlights can be refined only after extraction."))
	grounding.apply(doc)
	doc.save()
	return get_run(doc.name)


@frappe.whitelist()
def update_bbox(extraction, scope, page, bbox, fieldname=None, table=None, row_no=None, column=None):
	"""Persist a reviewer-corrected source region using normalized coordinates."""
	doc = _get_extraction(extraction, "write")
	if doc.status != "Review":
		frappe.throw(_("Highlights can be corrected only during review."))
	page = cint(page)
	page_row = next((item for item in doc.pages if cint(item.page_no) == page), None)
	if not page_row:
		frappe.throw(_("Unknown source page."))
	box = pipeline.validate_bbox_for_page(_as_dict(bbox), page_row.width, page_row.height)
	if not box:
		frappe.throw(_("Draw a valid source region."))

	row = None
	if scope == "field":
		row = next((item for item in doc.extracted_fields if item.fieldname == fieldname), None)
	elif scope == "table":
		row = next(
			(item for item in doc.lines if (item.table or doc.line_table) == table and cint(item.row_no) == cint(row_no)),
			None,
		)
	if not row:
		frappe.throw(_("The evidence target was not found."))

	data = _json_object(row.raw_json) if scope == "table" else {}
	cells = data.get("_cell_evidence") if isinstance(data.get("_cell_evidence"), dict) else {}
	if column:
		if scope != "table":
			frappe.throw(_("A column can only identify table evidence."))
		plugin = get_plugin(doc.target_doctype)
		schema = plugin.schema(ExtractionContext(doc.target_doctype))
		spec = next((item for item in schema.get("tables") or [] if item.get("table") == table), {})
		if column not in {item.get("key") for item in spec.get("columns") or []}:
			frappe.throw(_("Unknown evidence column."))
		previous = cells.get(column) if isinstance(cells.get(column), dict) else {}
	else:
		previous = _json_object(row.bbox_json)
	model = previous.get("model_bbox") if isinstance(previous.get("model_bbox"), dict) else {
		key: previous[key] for key in ("x", "y", "w", "h") if key in previous
	}
	box.update({"source": "manual", "verified": True, "score": 1})
	if len(model) == 4:
		box["model_bbox"] = model
		box["model_page"] = cint(previous.get("model_page")) or cint(row.source_page)
	if column:
		cells[column] = {**previous, **box, "page": page}
		data["_cell_evidence"] = cells
		row.raw_json = json.dumps(data, ensure_ascii=False)
	else:
		row.bbox_json = json.dumps(box, ensure_ascii=False)
		row.source_page = page
	doc.save()
	return get_run(doc.name)

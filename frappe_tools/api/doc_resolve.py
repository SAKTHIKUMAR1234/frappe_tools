"""Whitelisted review-action endpoints — thin dispatchers to the active plugin.

All document-specific behaviour (matching, item/supplier creation, learning) lives
in the plugin layer (frappe_tools.extractors). This module only routes UI calls to
the active plugin's capability for the extraction's target DocType.
"""

import frappe
from frappe.utils import cint

from frappe_tools.extractors import get_plugin
from frappe_tools.extractors.context import ExtractionContext


@frappe.whitelist()
def search_records(doctype, txt="", limit=20):
	"""Generic Link picker for the review UI."""
	frappe.has_permission(doctype, "read", throw=True)
	meta = frappe.get_meta(doctype)
	title_field = meta.get_title_field() if meta else "name"
	fields = ["name"]
	if title_field and title_field != "name":
		fields.append(f"{title_field} as label")
	filters = [[doctype, "name", "like", f"%{txt}%"]] if txt else []
	rows = frappe.get_all(doctype, filters=filters, fields=fields, limit=cint(limit) or 20)
	return [{"value": r["name"], "label": r.get("label") or r["name"]} for r in rows]


def _dispatch(extraction):
	doc = frappe.get_doc("Document Extraction", extraction)
	doc.check_permission("write")
	return doc, get_plugin(doc.target_doctype), ExtractionContext(doc.target_doctype)


@frappe.whitelist()
def confirm_line_item(extraction, row_no, item_code):
	doc, plugin, ctx = _dispatch(extraction)
	return plugin.confirm_row(ctx, doc, row_no, item_code)


@frappe.whitelist()
def set_line_freetext(extraction, row_no):
	doc, plugin, ctx = _dispatch(extraction)
	return plugin.free_text_row(ctx, doc, row_no)


@frappe.whitelist()
def create_item(extraction, row_no, item_group=None, stock_uom=None, item_name=None, item_code=None, hsn=None):
	doc, plugin, ctx = _dispatch(extraction)
	return plugin.create_row_master(ctx, doc, row_no, {
		"item_group": item_group, "stock_uom": stock_uom, "item_name": item_name,
		"item_code": item_code, "hsn": hsn,
	})


@frappe.whitelist()
def create_supplier(extraction, supplier_name=None, gstin=None, supplier_group=None):
	doc, plugin, ctx = _dispatch(extraction)
	res = plugin.create_link_record(ctx, doc, "supplier", {
		"supplier_name": supplier_name, "gstin": gstin, "supplier_group": supplier_group,
	}) or {}
	return {"ok": True, "supplier": res.get("value"), **res}

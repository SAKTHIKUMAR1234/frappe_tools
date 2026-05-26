"""Purchase Invoice review actions — create Item / Supplier, confirm + learn.

These back the plugin's review-action capabilities; the whitelisted endpoints in
api/doc_resolve.py dispatch here via the plugin.
"""

import re

import frappe
from frappe import _

from frappe_tools.extractors.base import get_line, set_field_value
from frappe_tools.utils import learning


def get_resolved_supplier(extraction):
	for f in extraction.extracted_fields:
		if f.fieldname == "supplier":
			val = f.value or f.matched_value
			if val and frappe.db.exists("Supplier", val):
				return val
	return None


def _learn(extraction, line, item_code):
	supplier = get_resolved_supplier(extraction)
	if not supplier:
		return
	learning.upsert_mapping(
		supplier, item_code,
		printed_text=line.description, code=line.supplier_code, uom=line.uom,
		extraction=extraction.name, target_doctype=extraction.target_doctype,
	)


def _default_supplier_group():
	return (
		frappe.db.get_single_value("Buying Settings", "supplier_group")
		or (frappe.get_all("Supplier Group", filters={"is_group": 0}, pluck="name", limit=1) or ["All Supplier Groups"])[0]
	)


def confirm_row(extraction, row_no, value):
	if not frappe.db.exists("Item", value):
		frappe.throw(_("Item {0} does not exist.").format(value))
	line = get_line(extraction, row_no)
	line.matched_item = value
	line.resolution_status = "Confirmed"
	line.match_method = "user-confirmed"
	line.match_confidence = 1.0
	extraction.save()
	_learn(extraction, line, value)
	return {"ok": True, "value": value, "item_code": value}


def create_row_master(extraction, row_no, opts):
	frappe.has_permission("Item", "create", throw=True)
	line = get_line(extraction, row_no)

	item = frappe.new_doc("Item")
	item.item_name = (opts.get("item_name") or line.description or "New Item")[:140]
	item.item_code = (opts.get("item_code") or item.item_name)[:140]
	item.item_group = opts.get("item_group")
	item.stock_uom = opts.get("stock_uom")
	item.is_purchase_item = 1
	hsn_source = opts.get("hsn") or line.hsn
	if hsn_source and frappe.get_meta("Item").has_field("gst_hsn_code"):
		digits = re.sub(r"\D", "", hsn_source)
		if len(digits) in (6, 8):  # GST sites reject malformed/short HSNs
			item.gst_hsn_code = digits
	if line.description:
		item.description = line.description
	item.insert()

	line.matched_item = item.name
	line.resolution_status = "New Item"
	line.match_method = "new-item"
	line.match_confidence = 1.0
	extraction.save()
	_learn(extraction, line, item.name)
	return {"ok": True, "value": item.name, "item_code": item.name, "item_name": item.item_name}


def create_supplier(extraction, opts):
	frappe.has_permission("Supplier", "create", throw=True)
	sup = frappe.new_doc("Supplier")
	sup.supplier_name = opts.get("supplier_name")
	sup.supplier_group = opts.get("supplier_group") or _default_supplier_group()
	if frappe.get_meta("Supplier").has_field("supplier_type") and not sup.get("supplier_type"):
		sup.supplier_type = "Company"
	gstin = opts.get("gstin")
	if gstin and frappe.get_meta("Supplier").has_field("gstin"):
		sup.gstin = gstin.strip().upper()
	sup.insert()
	set_field_value(extraction, "supplier", sup.name)
	extraction.save()
	return {"ok": True, "value": sup.name, "supplier": sup.name}

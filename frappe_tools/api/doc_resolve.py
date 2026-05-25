"""Shared master-data matching primitives + the review-UI endpoints.

The per-doctype adapter (e.g. the Purchase Invoice adapter) orchestrates these
into its cascade. Learning write-back goes to the `Supplier Item Map` store via
frappe_tools.utils.learning.
"""

import difflib
import json
import re

import frappe
from frappe import _
from frappe.utils import cint

from frappe_tools.utils import learning

CANDIDATE_LIMIT = 8
AUTO_MATCH_THRESHOLD = 0.82


def _norm(s):
	return " ".join((s or "").strip().lower().split())


def _sim(a, b):
	if not a or not b:
		return 0.0
	return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _has_field(doctype, fieldname):
	try:
		return bool(frappe.get_meta(doctype).has_field(fieldname))
	except Exception:
		return False


def _narrowed(doctype, text, base_filters, fields, name_field=None, hard_limit=400):
	"""Candidate rows whose name/label LIKE the longest token, else a capped slice."""
	tokens = [t for t in _norm(text).split() if len(t) >= 4]
	tokens.sort(key=len, reverse=True)
	rows = []
	if tokens:
		or_filters = [[doctype, "name", "like", f"%{tokens[0]}%"]]
		if name_field:
			or_filters.append([doctype, name_field, "like", f"%{tokens[0]}%"])
		rows = frappe.get_all(doctype, filters=base_filters, or_filters=or_filters, fields=fields, limit=hard_limit)
	if not rows:
		rows = frappe.get_all(doctype, filters=base_filters, fields=fields, limit=hard_limit)
	return rows


# --------------------------------------------------------------------------
# Supplier resolution
# --------------------------------------------------------------------------

def resolve_supplier(printed_name, gstin=None):
	"""Resolve a printed supplier to an existing Supplier. GSTIN > exact > fuzzy."""
	result = {"matched": None, "method": None, "candidates": []}

	if gstin:
		g = gstin.strip().upper()
		names = []
		if _has_field("Supplier", "gstin"):
			names += frappe.get_all("Supplier", filters={"gstin": g}, pluck="name")
		if not names and _has_field("Address", "gstin"):
			for a in frappe.get_all("Address", filters={"gstin": g}, pluck="name"):
				names += frappe.get_all(
					"Dynamic Link",
					filters={"parenttype": "Address", "parent": a, "link_doctype": "Supplier"},
					pluck="link_name",
				)
		names = list(dict.fromkeys(n for n in names if n))
		if names:
			result["candidates"] = [_supplier_cand(n, 1.0, "gstin") for n in names[:CANDIDATE_LIMIT]]
			result["matched"] = names[0]
			result["method"] = "gstin"
			return result

	if printed_name and frappe.db.exists("Supplier", printed_name):
		result["candidates"] = [_supplier_cand(printed_name, 1.0, "exact-name")]
		result["matched"] = printed_name
		result["method"] = "exact-name"
		return result

	if printed_name:
		rows = _narrowed("Supplier", printed_name, {"disabled": 0}, ["name", "supplier_name"])
		scored = []
		for r in rows:
			score = max(_sim(printed_name, r.get("supplier_name")), _sim(printed_name, r.get("name")))
			if score > 0.4:
				scored.append((score, r["name"]))
		scored.sort(reverse=True)
		result["candidates"] = [_supplier_cand(n, round(s, 3), "fuzzy") for s, n in scored[:CANDIDATE_LIMIT]]
		if scored and scored[0][0] >= AUTO_MATCH_THRESHOLD:
			result["matched"] = scored[0][1]
			result["method"] = "fuzzy"

	return result


def _supplier_cand(name, score, method):
	return {"value": name, "label": frappe.db.get_value("Supplier", name, "supplier_name") or name, "score": score, "method": method}


# --------------------------------------------------------------------------
# Item resolution (lexical tiers — supplier-part-no, barcode, exact, fuzzy)
# --------------------------------------------------------------------------

def resolve_item(supplier, supplier_code, description, hsn=None):
	if supplier and supplier_code:
		rows = frappe.get_all(
			"Item Supplier",
			filters={"supplier": supplier, "supplier_part_no": supplier_code},
			pluck="parent",
			limit=5,
		)
		if rows:
			return _item_result(rows[0], 1.0, "supplier-part-no")

	if supplier_code:
		bc = frappe.get_all("Item Barcode", filters={"barcode": supplier_code}, pluck="parent", limit=1)
		if bc:
			return _item_result(bc[0], 1.0, "barcode")

	if supplier_code and frappe.db.exists("Item", supplier_code):
		return _item_result(supplier_code, 1.0, "exact-code")

	candidates = _fuzzy_items(description, hsn)
	result = {"matched": None, "method": None, "confidence": 0.0, "candidates": candidates}
	if candidates and candidates[0]["score"] >= AUTO_MATCH_THRESHOLD:
		result.update(matched=candidates[0]["value"], method=candidates[0]["method"], confidence=candidates[0]["score"])
	return result


def _item_result(item_code, score, method):
	return {"matched": item_code, "method": method, "confidence": score, "candidates": [_item_cand(item_code, score, method)]}


def candidate_item_rows(description, hsn=None, limit=300):
	"""Raw candidate Item rows (shared by lexical fuzzy + semantic ranking)."""
	base = {"disabled": 0, "is_purchase_item": 1, "has_variants": 0}
	rows = []
	if hsn and _has_field("Item", "gst_hsn_code"):
		rows = frappe.get_all("Item", filters={**base, "gst_hsn_code": hsn}, fields=["name", "item_name", "description"], limit=limit)
	if not rows:
		rows = _narrowed("Item", description, base, ["name", "item_name", "description"], name_field="item_name", hard_limit=limit)
	return rows


def _fuzzy_items(description, hsn=None):
	rows = candidate_item_rows(description, hsn)
	method = "hsn-fuzzy" if (hsn and rows) else "fuzzy"
	scored = []
	for r in rows:
		score = max(_sim(description, r.get("item_name")), _sim(description, r.get("name")), 0.6 * _sim(description, r.get("description")))
		if score > 0.35:
			scored.append((score, r))
	scored.sort(key=lambda x: x[0], reverse=True)
	return [_item_cand(r["name"], round(s, 3), method, r.get("item_name")) for s, r in scored[:CANDIDATE_LIMIT]]


def _item_cand(item_code, score, method, item_name=None):
	if item_name is None:
		item_name = frappe.db.get_value("Item", item_code, "item_name") or item_code
	return {"value": item_code, "label": item_name, "score": score, "method": method}


# --------------------------------------------------------------------------
# Review-UI endpoints
# --------------------------------------------------------------------------

@frappe.whitelist()
def search_records(doctype, txt="", limit=20):
	frappe.has_permission(doctype, "read", throw=True)
	meta = frappe.get_meta(doctype)
	title_field = meta.get_title_field() if meta else "name"
	fields = ["name"]
	if title_field and title_field != "name":
		fields.append(f"{title_field} as label")
	filters = [[doctype, "name", "like", f"%{txt}%"]] if txt else []
	rows = frappe.get_all(doctype, filters=filters, fields=fields, limit=cint(limit) or 20)
	return [{"value": r["name"], "label": r.get("label") or r["name"]} for r in rows]


@frappe.whitelist()
def confirm_line_item(extraction, row_no, item_code):
	"""Confirm a line->Item mapping and learn it for next time."""
	doc = frappe.get_doc("Document Extraction", extraction)
	doc.check_permission("write")
	if not frappe.db.exists("Item", item_code):
		frappe.throw(_("Item {0} does not exist.").format(item_code))

	line = _get_line(doc, row_no)
	line.matched_item = item_code
	line.resolution_status = "Confirmed"
	line.match_method = "user-confirmed"
	line.match_confidence = 1.0
	doc.save()

	_learn(doc, line, item_code)
	return {"ok": True, "item_code": item_code}


@frappe.whitelist()
def set_line_freetext(extraction, row_no):
	doc = frappe.get_doc("Document Extraction", extraction)
	doc.check_permission("write")
	line = _get_line(doc, row_no)
	line.matched_item = None
	line.resolution_status = "Free Text"
	doc.save()
	return {"ok": True}


@frappe.whitelist()
def create_item(extraction, row_no, item_group, stock_uom, item_name=None, item_code=None, hsn=None):
	doc = frappe.get_doc("Document Extraction", extraction)
	doc.check_permission("write")
	frappe.has_permission("Item", "create", throw=True)

	line = _get_line(doc, row_no)
	item = frappe.new_doc("Item")
	item.item_name = (item_name or line.description or "New Item")[:140]
	# Items can be named by Item Code on some sites; default it to the name.
	item.item_code = (item_code or item.item_name)[:140]
	item.item_group = item_group
	item.stock_uom = stock_uom
	item.is_purchase_item = 1
	# HSN: prefer the value the user supplied in the dialog, else the printed line HSN.
	# Only set a valid 6/8-digit code (GST-enabled sites reject malformed/short ones).
	hsn_source = hsn or line.hsn
	if hsn_source and _has_field("Item", "gst_hsn_code"):
		hsn_digits = re.sub(r"\D", "", hsn_source)
		if len(hsn_digits) in (6, 8):
			item.gst_hsn_code = hsn_digits
	if line.description:
		item.description = line.description
	item.insert()

	line.matched_item = item.name
	line.resolution_status = "New Item"
	line.match_method = "new-item"
	line.match_confidence = 1.0
	doc.save()

	_learn(doc, line, item.name)
	return {"ok": True, "item_code": item.name, "item_name": item.item_name}


@frappe.whitelist()
def create_supplier(extraction, supplier_name, gstin=None, supplier_group=None):
	doc = frappe.get_doc("Document Extraction", extraction)
	doc.check_permission("write")
	frappe.has_permission("Supplier", "create", throw=True)

	supplier = frappe.new_doc("Supplier")
	supplier.supplier_name = supplier_name
	if supplier_group:
		supplier.supplier_group = supplier_group
	if gstin and _has_field("Supplier", "gstin"):
		supplier.gstin = gstin.strip().upper()
	supplier.insert()

	for f in doc.extracted_fields:
		if f.fieldname == "supplier":
			f.value = supplier.name
			f.matched_value = supplier.name
			f.match_method = "user-created"
			f.status = "Edited"
	doc.save()
	return {"ok": True, "supplier": supplier.name}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _get_line(doc, row_no):
	line = next((l for l in doc.lines if cint(l.row_no) == cint(row_no)), None)
	if not line:
		frappe.throw(_("Line {0} not found on this extraction.").format(row_no))
	return line


def get_resolved_supplier(doc):
	for f in doc.extracted_fields:
		if f.fieldname == "supplier":
			val = f.value or f.matched_value
			if val and frappe.db.exists("Supplier", val):
				return val
	return None


def _learn(doc, line, item_code):
	supplier = get_resolved_supplier(doc)
	if not supplier:
		return
	learning.upsert_mapping(
		supplier,
		item_code,
		printed_text=line.description,
		code=line.supplier_code,
		uom=line.uom,
		extraction=doc.name,
		target_doctype=doc.target_doctype,
	)

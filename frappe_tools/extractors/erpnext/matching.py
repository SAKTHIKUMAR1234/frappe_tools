"""ERPNext master-data matching primitives — shared across ERPNext plugins.

Pure functions (no whitelisting, no side effects) for resolving printed text to
Supplier / Item records. Plugins orchestrate these into their cascade.
"""

import difflib

import frappe

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


# ----- Supplier ------------------------------------------------------------

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


# ----- Item ----------------------------------------------------------------

def resolve_item(supplier, supplier_code, description, hsn=None):
	if supplier and supplier_code:
		rows = frappe.get_all("Item Supplier", filters={"supplier": supplier, "supplier_part_no": supplier_code}, pluck="parent", limit=5)
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

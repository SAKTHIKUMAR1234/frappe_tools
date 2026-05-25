"""ERPNext Purchase Invoice adapter.

Owns everything Purchase-Invoice-specific: which fields/lines to extract, the
supplier + item resolution cascade (deterministic memory -> lexical tiers ->
semantic -> suggestion), validation, the save-only document build, and learning
write-back. Registered against (ERPNext, Purchase Invoice).
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from frappe_tools.adapters import register
from frappe_tools.adapters.base import ExtractionAdapter
from frappe_tools.api import doc_resolve
from frappe_tools.utils import coerce, embeddings, learning

AUTO_SUGGEST_THRESHOLD = 0.82


@register
class PurchaseInvoiceAdapter(ExtractionAdapter):
	system = "ERPNext"
	target_doctype = "Purchase Invoice"

	HEADER = [
		{"fieldname": "supplier", "label": "Supplier", "fieldtype": "Link", "link_doctype": "Supplier",
		 "required": True, "description": "Supplier / vendor name exactly as printed on the invoice."},
		{"fieldname": "bill_no", "label": "Supplier Invoice No", "fieldtype": "Data", "required": True,
		 "description": "The supplier's own invoice/bill number printed on the document."},
		{"fieldname": "bill_date", "label": "Supplier Invoice Date", "fieldtype": "Date",
		 "description": "Date printed on the supplier invoice."},
		{"fieldname": "posting_date", "label": "Posting Date", "fieldtype": "Date",
		 "description": "Use the invoice date unless a distinct received/posting date is shown."},
		{"fieldname": "supplier_gstin", "label": "Supplier GSTIN", "fieldtype": "Data",
		 "description": "Supplier's 15-character GSTIN if printed."},
	]

	LINE_COLUMNS = [
		{"key": "description", "label": "Description", "type": "text",
		 "description": "Item/service description exactly as printed for the line."},
		{"key": "supplier_code", "label": "Supplier Item Code", "type": "text",
		 "description": "Any item/SKU/part code the supplier printed next to the line (not the HSN)."},
		{"key": "hsn", "label": "HSN/SAC", "type": "text", "description": "HSN or SAC code if printed."},
		{"key": "qty", "label": "Qty", "type": "number"},
		{"key": "uom", "label": "UOM", "type": "text", "description": "Unit such as Nos, Kg, Mtr; blank if not shown."},
		{"key": "rate", "label": "Rate", "type": "number", "description": "Unit price before tax."},
		{"key": "amount", "label": "Amount", "type": "number", "description": "Line amount before tax (qty x rate)."},
	]

	# ----- extraction shape -------------------------------------------------
	def header_fields(self):
		return self.HEADER

	def line_config(self):
		return {"table": "items", "columns": self.LINE_COLUMNS}

	def prompt_addendum(self):
		return (
			"This is a SUPPLIER (purchase) invoice we are recording as a Purchase Invoice. "
			"Capture the supplier's identity (name + GSTIN) and every line item with the supplier's "
			"exact wording and any code printed beside it. Do not compute taxes; report printed "
			"rate/amount as-is. Treat charges/services (e.g. 'dyeing charges') as line items too."
		)

	def review_policy(self):
		return {"supplier": "confirm", "bill_no": "confirm"}

	# ----- resolution -------------------------------------------------------
	def resolve(self, extraction):
		supplier = self._resolve_supplier(extraction)
		for line in extraction.lines:
			self._resolve_line(line, supplier)

	def _resolve_supplier(self, extraction):
		name_field = self._field(extraction, "supplier")
		gstin_field = self._field(extraction, "supplier_gstin")
		printed = (name_field.llm_value or name_field.value) if name_field else None
		gstin = (gstin_field.value or gstin_field.llm_value) if gstin_field else None

		res = doc_resolve.resolve_supplier(printed, gstin)
		if name_field:
			name_field.candidates_json = json.dumps(res["candidates"])
			name_field.match_method = res["method"]
			if res["matched"]:
				name_field.matched_value = res["matched"]
				name_field.value = res["matched"]
				if res["method"] in ("gstin", "exact-name"):
					name_field.status = "Approved"
		return res["matched"]

	def _resolve_line(self, line, supplier):
		desc = line.description or ""
		code = (line.supplier_code or "").strip()

		# Tier 0 — deterministic memory (auto-applies; exact only).
		mem = learning.lookup_memory(supplier, desc, code, target_doctype=self.target_doctype)
		if mem:
			label = frappe.db.get_value("Item", mem["item_code"], "item_name") or mem["item_code"]
			line.matched_item = mem["item_code"]
			line.match_method = "memory"
			line.match_confidence = 1.0
			line.resolution_status = "Matched"
			if mem.get("uom") and not line.uom:
				line.uom = mem["uom"]
			line.candidates_json = json.dumps([{"value": mem["item_code"], "label": label, "score": 1.0, "method": "memory"}])
			return

		# Tiers 1-5 — lexical (supplier-part-no / barcode / exact / fuzzy).
		res = doc_resolve.resolve_item(supplier, code, desc, line.hsn)
		candidates = list(res.get("candidates") or [])

		# Semantic layer (suggestion only).
		if embeddings.is_enabled() and desc:
			rows = doc_resolve.candidate_item_rows(desc, line.hsn, limit=60)
			candidates = _merge_candidates(candidates, embeddings.rank_items_semantic(desc, rows, limit=8))

		candidates.sort(key=lambda c: c.get("score", 0), reverse=True)
		candidates = candidates[:8]
		line.candidates_json = json.dumps(candidates)

		if res.get("matched"):
			line.matched_item = res["matched"]
			line.match_method = res["method"]
			line.match_confidence = res.get("confidence", 0)
			line.resolution_status = "Matched"
		elif candidates and candidates[0]["score"] >= AUTO_SUGGEST_THRESHOLD:
			top = candidates[0]
			line.matched_item = top["value"]
			line.match_method = top["method"]
			line.match_confidence = top["score"]
			line.resolution_status = "Matched"
		else:
			line.resolution_status = "Unmatched"

	# ----- validation -------------------------------------------------------
	def validate(self, extraction):
		issues = []
		if not doc_resolve.get_resolved_supplier(extraction):
			issues.append(_("Supplier is not resolved to an existing record."))
		if not any(l.resolution_status != "Rejected" for l in extraction.lines):
			issues.append(_("There are no line items to post."))
		return issues

	# ----- build & post (save only) ----------------------------------------
	def build_document(self, extraction):
		target = frappe.new_doc("Purchase Invoice")
		company = _default_company()
		if company:
			target.company = company

		for f in extraction.extracted_fields:
			if f.status == "Rejected" or not f.value:
				continue
			if f.fieldname == "supplier_gstin":  # read-only, auto-fetched from supplier
				continue
			target.set(f.fieldname, coerce.coerce_value(f.fieldtype, f.value))

		if not target.supplier:
			frappe.throw(_("Resolve the supplier before creating the Purchase Invoice."))
		if not target.posting_date:
			target.posting_date = nowdate()

		posted_lines = []
		for line in extraction.lines:
			if line.resolution_status == "Rejected":
				continue
			row = {"qty": flt(line.qty) or 1, "rate": flt(line.rate)}
			if line.matched_item and line.resolution_status != "Free Text":
				row["item_code"] = line.matched_item
				if line.uom:
					row["uom"] = line.uom
					row["conversion_factor"] = 1
			else:
				row["item_name"] = (line.description or "Item")[:140]
				row["description"] = line.description
				row["uom"] = line.uom or "Nos"
				row["conversion_factor"] = 1
			target.append("items", row)
			posted_lines.append(line)

		if not posted_lines:
			frappe.throw(_("There are no line items to post."))

		target.insert()  # save only — never submit

		# Learn the accepted mappings (review-then-create = acceptance).
		supplier = target.supplier
		for line in posted_lines:
			if line.matched_item and line.resolution_status != "Free Text" and supplier:
				learning.upsert_mapping(
					supplier, line.matched_item,
					printed_text=line.description, code=line.supplier_code,
					uom=line.uom, extraction=extraction.name, target_doctype=self.target_doctype,
				)

		return target.name

	# ----- helpers ----------------------------------------------------------
	def _field(self, extraction, fieldname):
		return next((f for f in extraction.extracted_fields if f.fieldname == fieldname), None)


def _merge_candidates(a, b):
	by = {}
	for c in (a or []) + (b or []):
		v = c.get("value")
		if not v:
			continue
		if v not in by or c.get("score", 0) > by[v].get("score", 0):
			by[v] = c
	return list(by.values())


def _default_company():
	return (
		frappe.defaults.get_user_default("Company")
		or frappe.defaults.get_global_default("company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
	)

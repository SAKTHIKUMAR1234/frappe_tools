"""Supplier + item resolution cascade for Purchase Invoice line items.

Uses the shared matching primitives (frappe_tools.api.doc_resolve), the learned
memory store (utils.learning), and the optional semantic layer (utils.embeddings).
"""

import json

import frappe

from frappe_tools.extractors.erpnext import matching
from frappe_tools.utils import embeddings, learning

TABLE = "items"
AUTO_SUGGEST_THRESHOLD = 0.82


def resolve(ctx, extraction):
	supplier = _resolve_supplier(extraction)
	for line in extraction.lines:
		if (line.table or extraction.line_table) != TABLE:
			continue
		_resolve_line(line, supplier)


def _resolve_supplier(extraction):
	name_field = _field(extraction, "supplier")
	gstin_field = _field(extraction, "supplier_gstin")
	printed = (name_field.llm_value or name_field.value) if name_field else None
	gstin = (gstin_field.value or gstin_field.llm_value) if gstin_field else None

	res = matching.resolve_supplier(printed, gstin)
	if name_field:
		name_field.candidates_json = json.dumps(res["candidates"])
		name_field.match_method = res["method"]
		if res["matched"]:
			name_field.matched_value = res["matched"]
			name_field.value = res["matched"]
			if res["method"] in ("gstin", "exact-name"):
				name_field.status = "Approved"
	return res["matched"]


def _resolve_line(line, supplier):
	desc = line.description or ""
	code = (line.supplier_code or "").strip()

	# Tier 0 — deterministic memory (auto-applies; exact only).
	mem = learning.lookup_memory(supplier, desc, code, target_doctype="Purchase Invoice")
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
	res = matching.resolve_item(supplier, code, desc, line.hsn)
	candidates = list(res.get("candidates") or [])

	# Semantic layer (suggestion only).
	if embeddings.is_enabled() and desc:
		rows = matching.candidate_item_rows(desc, line.hsn, limit=60)
		candidates = _merge(candidates, embeddings.rank_items_semantic(desc, rows, limit=8))

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


def _field(extraction, fieldname):
	return next((f for f in extraction.extracted_fields if f.fieldname == fieldname), None)


def _merge(a, b):
	by = {}
	for c in (a or []) + (b or []):
		v = c.get("value")
		if not v:
			continue
		if v not in by or c.get("score", 0) > by[v].get("score", 0):
			by[v] = c
	return list(by.values())

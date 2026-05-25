"""Learning store operations on `Supplier Item Map`.

The deterministic per-supplier memory: confirmed (supplier + printed text/code) →
item mappings. `lookup_memory` is the tier-1 auto-apply match; `upsert_mapping`
is the write-back called on every human confirmation (the flywheel). System- and
target-doctype-scoped so the same store serves future adapters.
"""

import json

import frappe
from frappe.utils import cint, now_datetime

from frappe_tools.utils import embeddings

MAP_DOCTYPE = "Supplier Item Map"


def normalize(text):
	return " ".join((text or "").strip().lower().split())


def lookup_memory(supplier, text, code=None, target_doctype="Purchase Invoice", system="ERPNext"):
	"""Return the remembered mapping for this supplier's printed text/code, or None.

	Exact supplier-code match wins over exact normalized-text match. This is the
	only tier allowed to auto-apply (per the one-confirmation policy)."""
	if not supplier:
		return None
	scope = {"supplier": supplier, "source_system": system, "target_doctype": target_doctype}

	if code:
		row = frappe.get_all(
			MAP_DOCTYPE,
			filters={**scope, "supplier_code": code},
			fields=["item_code", "uom", "name"],
			order_by="hit_count desc",
			limit=1,
		)
		if row and frappe.db.exists("Item", row[0].item_code):
			return row[0]

	norm = normalize(text)
	if norm:
		row = frappe.get_all(
			MAP_DOCTYPE,
			filters={**scope, "normalized_text": norm},
			fields=["item_code", "uom", "name"],
			order_by="hit_count desc",
			limit=1,
		)
		if row and frappe.db.exists("Item", row[0].item_code):
			return row[0]

	return None


def upsert_mapping(
	supplier,
	item_code,
	printed_text,
	code=None,
	uom=None,
	extraction=None,
	target_doctype="Purchase Invoice",
	system="ERPNext",
):
	"""Create or reinforce a learned mapping. Returns the Supplier Item Map name."""
	if not supplier or not item_code:
		return None

	norm = normalize(printed_text)
	scope = {"supplier": supplier, "source_system": system, "target_doctype": target_doctype}

	existing = frappe.get_all(
		MAP_DOCTYPE,
		filters={**scope, "normalized_text": norm, "item_code": item_code},
		pluck="name",
		limit=1,
	)
	if existing:
		doc = frappe.get_doc(MAP_DOCTYPE, existing[0])
		doc.hit_count = cint(doc.hit_count) + 1
		doc.last_used = now_datetime()
		if code and not doc.supplier_code:
			doc.supplier_code = code
		doc.save(ignore_permissions=True)
		return doc.name

	doc = frappe.new_doc(MAP_DOCTYPE)
	doc.update(
		{
			"supplier": supplier,
			"item_code": item_code,
			"printed_text": printed_text,
			"normalized_text": norm,
			"supplier_code": code,
			"uom": uom,
			"hit_count": 1,
			"confidence": 1.0,
			"last_used": now_datetime(),
			"source_system": system,
			"target_doctype": target_doctype,
			"created_from_extraction": extraction,
		}
	)
	if norm and embeddings.is_enabled():
		vec = embeddings.embed(norm)
		if vec:
			doc.text_embedding = json.dumps(vec)

	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return doc.name

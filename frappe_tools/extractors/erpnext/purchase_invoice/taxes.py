"""Tax handling for Purchase Invoice — app-aware (India Compliance).

Decision: the extraction governs taxes; ERPNext must NOT silently auto-apply the
item's configured tax template on the draft. This baseline clears the auto-applied
template so taxes don't appear out of nowhere. Full per-line forcing of
nil/0%/exempt needs an extracted tax column (see docs/extraction-architecture.md §7)
— a tracked follow-up. Always safe: never breaks the save.
"""

import frappe


def customize(ctx, doc, extraction):
	if not ctx.has_app("india_compliance"):
		return
	try:
		if doc.meta.has_field("taxes_and_charges"):
			doc.taxes_and_charges = None
		for row in (doc.get("items") or []):
			if hasattr(row, "item_tax_template"):
				row.item_tax_template = None
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Purchase Invoice tax customize failed")

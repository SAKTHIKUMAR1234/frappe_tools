"""Row reshaping before build — Purchase Invoice line consolidation.

GENERAL behaviour keeps rows 1:1. Here, when the rule book enables consolidation
with a common item, collapse all extracted item lines into one common-item line
(the production pattern: book the whole invoice against a single item).
"""

from frappe.utils import flt

from frappe_tools.extractors.erpnext.purchase_invoice import config


def transform(ctx, extraction, build):
	cfg = config.consolidation(extraction)
	if not cfg["consolidate"] or not cfg["common_item"]:
		return

	rows = build.get("items")
	if not rows or len(rows) <= 1:
		return

	total = 0.0
	for r in rows:
		amt = flt(r.get("amount")) or (flt(r.get("qty")) * flt(r.get("rate")))
		total += amt

	build["items"] = [{
		"item_code": cfg["common_item"],
		"qty": 1,
		"rate": total,
		"description": "Consolidated from {0} invoice lines".format(len(rows)),
	}]

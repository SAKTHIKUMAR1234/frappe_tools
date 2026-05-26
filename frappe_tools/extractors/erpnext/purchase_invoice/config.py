"""Purchase-Invoice extension config — read from the rule book (per-supplier later)."""

import frappe


def consolidation(extraction):
	"""Return {"consolidate": bool, "common_item": item_code|None} from the rule book."""
	rb = frappe.get_all(
		"Document Rule Book",
		filters={"target_doctype": extraction.target_doctype, "enabled": 1, "consolidate_lines": 1},
		fields=["common_item"],
		limit=1,
	)
	if rb and rb[0].common_item:
		return {"consolidate": True, "common_item": rb[0].common_item}
	return {"consolidate": False, "common_item": None}

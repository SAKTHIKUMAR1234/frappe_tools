"""Initialize new AI settings on existing singleton records."""

import frappe


def execute() -> None:
	settings = frappe.db.get_singles_dict("Document Scanner Settings")
	defaults = {
		"ai_confidence_threshold": 0.8, "ai_batch_limit": 200,
		"page_extraction_batch_limit": 500,
	}
	for field, value in defaults.items():
		if settings.get(field) in (None, ""):
			frappe.db.set_single_value("Document Scanner Settings", field, value)

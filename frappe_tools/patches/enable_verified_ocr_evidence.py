import frappe


def execute():
	"""Enable the safe evidence gate on sites created before the setting existed."""
	frappe.db.set_single_value(
		"Document Extraction Settings",
		"require_verified_evidence",
		1,
	)

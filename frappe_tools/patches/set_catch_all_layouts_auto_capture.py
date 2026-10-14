import frappe


def execute() -> None:
	"""Keep auto-capture on for catch-all layouts, as before the per-layout flag."""
	frappe.db.set_value(
		"Document Scanner Layout", {"is_catch_all": 1, "auto_capture": 0}, "auto_capture", 1,
		update_modified=False,
	)

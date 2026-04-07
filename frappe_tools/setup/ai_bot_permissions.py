import frappe


ROLE_NAME = "AI Bot"
BATCH_SIZE = 100


def setup_ai_bot_permissions():
	"""Ensure the AI Bot role exists and has access to role-restricted reports.

	Doctype-level read access is granted at runtime by the has_permission hook
	in frappe_tools.permissions, so no DocPerm rows are written here — those
	get wiped by 'Restore Original Permissions' and bench migrate.

	Reports use a separate 'Has Role' child table that survives restores, so
	we still need to add AI Bot to reports that have role restrictions.
	"""
	ensure_role_exists()
	setup_report_permissions()


def setup_report_permissions():
	"""Add AI Bot to the Has Role table of every report that has role restrictions.

	Reports in Frappe use a separate access control via the 'Has Role' child table.
	If a report has roles listed, the user must hold one of those roles — even if
	they have 'report' permission on the underlying doctype. Reports with no roles
	are accessible to everyone, so those are skipped.
	"""
	# Reports that already have AI Bot in their Has Role
	existing_ai_bot = set(
		frappe.get_all(
			"Has Role",
			filters={"parenttype": "Report", "role": ROLE_NAME},
			pluck="parent",
		)
	)

	# Reports that have at least one role restriction (but not AI Bot)
	all_report_roles = frappe.get_all(
		"Has Role",
		filters={"parenttype": "Report"},
		fields=["parent"],
		group_by="parent",
		pluck="parent",
	)
	reports_needing_access = set(all_report_roles) - existing_ai_bot

	count = 0
	for report_name in reports_needing_access:
		doc = frappe.new_doc("Has Role")
		doc.parent = report_name
		doc.parenttype = "Report"
		doc.parentfield = "roles"
		doc.role = ROLE_NAME
		doc.db_insert()

		count += 1
		if count % BATCH_SIZE == 0:
			frappe.db.commit()

	if count:
		frappe.db.commit()
		frappe.clear_cache()


def ensure_role_exists():
	"""Create the AI Bot role if it doesn't exist yet."""
	if frappe.db.exists("Role", ROLE_NAME):
		return
	role = frappe.new_doc("Role")
	role.role_name = ROLE_NAME
	role.desk_access = 0
	role.insert(ignore_permissions=True)
	frappe.db.commit()

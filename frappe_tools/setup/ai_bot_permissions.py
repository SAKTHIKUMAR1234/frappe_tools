import frappe


ROLE_NAME = "AI Bot"
BATCH_SIZE = 100


def setup_ai_bot_permissions():
	"""Grant read+select permission on all doctypes and reports to the AI Bot role.

	Runs on after_install and after_migrate. Idempotent — skips doctypes/reports
	that already have an AI Bot permission entry.
	"""
	ensure_role_exists()
	setup_doctype_permissions()
	setup_report_permissions()


def setup_doctype_permissions():
	"""Grant read+select DocPerm on all non-child, non-virtual doctypes."""
	all_doctypes = frappe.get_all(
		"DocType",
		filters={"istable": 0, "is_virtual": 0},
		pluck="name",
	)

	# Doctypes that already have AI Bot in DocPerm
	existing_ai_bot = set(
		frappe.get_all(
			"DocPerm",
			filters={"role": ROLE_NAME, "permlevel": 0},
			pluck="parent",
		)
	)

	count = 0
	for dt in all_doctypes:
		if dt in existing_ai_bot:
			continue

		doc = frappe.new_doc("DocPerm")
		doc.parent = dt
		doc.parenttype = "DocType"
		doc.parentfield = "permissions"
		doc.role = ROLE_NAME
		doc.permlevel = 0
		# Explicitly set desired permissions
		doc.read = 1
		doc.select = 1
		doc.report = 1
		doc.export = 1
		doc.print = 1
		# Explicitly disable — DB defaults these to 1
		doc.create = 0
		doc.write = 0
		doc.delete = 0
		doc.share = 0
		doc.email = 0
		doc.db_insert()

		count += 1
		if count % BATCH_SIZE == 0:
			frappe.db.commit()

	if count:
		frappe.db.commit()
		frappe.clear_cache()


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

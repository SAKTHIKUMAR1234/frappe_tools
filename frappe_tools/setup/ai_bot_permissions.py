import frappe


ROLE_NAME = "AI Bot"
BATCH_SIZE = 100


def setup_ai_bot_permissions():
	"""Grant read+select permission on all doctypes to the AI Bot role.

	Runs on after_install and after_migrate. Idempotent — skips doctypes
	that already have an AI Bot DocPerm entry.
	"""
	ensure_role_exists()

	# All non-child, non-virtual doctypes
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


def ensure_role_exists():
	"""Create the AI Bot role if it doesn't exist yet."""
	if frappe.db.exists("Role", ROLE_NAME):
		return
	role = frappe.new_doc("Role")
	role.role_name = ROLE_NAME
	role.desk_access = 0
	role.insert(ignore_permissions=True)
	frappe.db.commit()

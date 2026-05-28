import frappe


ROLE_NAME = "AI Bot"
BATCH_SIZE = 200

# Read-style ptypes AI Bot is granted on every (doctype, permlevel) pair.
# Anything not listed here (write, create, delete, submit, cancel, amend,
# share, set_user_permissions) stays at 0 — AI Bot is read-only.
_AI_BOT_PTYPES = ("read", "select", "report", "print", "email", "export")


def setup_ai_bot_permissions():
	"""Ensure AI Bot has read access to every DocType, Report, and Page.

	DocType access is granted by inserting AI Bot rows into whichever permission
	table the doctype is already using:

	- doctype has Custom DocPerm rows → write AI Bot row into Custom DocPerm.
	- doctype has only standard DocPerm rows → write AI Bot row into DocPerm.

	This matches Frappe's own convention (once a doctype gains a Custom DocPerm
	row, its standard DocPerm rows are suppressed wholesale) and keeps the setup
	strictly additive — no existing row is ever modified or moved.

	Reports and Pages are handled separately via Has Role rows, since their
	role gates live there rather than in DocPerm.
	"""
	ensure_role_exists()
	setup_doctype_permissions()
	setup_report_permissions()
	setup_page_permissions()
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


# ---------------- DocType perms ----------------------------------------------


def setup_doctype_permissions():
	"""Add AI Bot read rows to every DocType at every permlevel it uses.

	For each doctype we:
	  1. Pick the target table — Custom DocPerm if the doctype already has any
	     Custom DocPerm rows, otherwise standard DocPerm. This mirrors Frappe's
	     own override rule (any Custom DocPerm row on a doctype suppresses every
	     standard DocPerm row), so we always land in the active permission set.
	  2. Collect every distinct permlevel present on the doctype's fields plus
	     Custom Fields, including 0. This ensures permlevel-1/2/… fields become
	     readable via Meta.get_permlevel_access.
	  3. Skip (doctype, permlevel) pairs that already have an AI Bot row in the
	     chosen table, so re-runs are no-ops.

	Wired via `after_migrate`, so bench migrate will re-add any row that gets
	wiped when a stock DocType's permissions are re-synced from JSON.
	"""
	doctypes = frappe.get_all("DocType", pluck="name")

	doctypes_with_custom = set(
		frappe.get_all("Custom DocPerm", pluck="parent", distinct=True)
	)
	existing_in_custom = _existing_ai_bot_rows("Custom DocPerm")
	existing_in_standard = _existing_ai_bot_rows("DocPerm")

	added = 0
	for doctype in doctypes:
		uses_custom = doctype in doctypes_with_custom
		target = "Custom DocPerm" if uses_custom else "DocPerm"
		existing = existing_in_custom if uses_custom else existing_in_standard

		for level in _collect_permlevels(doctype):
			if (doctype, level) in existing:
				continue
			_insert_ai_bot_row(target, doctype, level)
			existing.add((doctype, level))
			added += 1
			if added % BATCH_SIZE == 0:
				frappe.db.commit()

	if added:
		frappe.db.commit()


def _existing_ai_bot_rows(table):
	"""Set of (parent, permlevel) where AI Bot already has a row in `table`."""
	return {
		(row.parent, int(row.permlevel or 0))
		for row in frappe.get_all(
			table,
			filters={"role": ROLE_NAME},
			fields=["parent", "permlevel"],
		)
	}


def _collect_permlevels(doctype):
	"""Every distinct permlevel found on the doctype's fields + Custom Fields.

	Always includes 0 so doctypes with no permlevel-tagged fields still get
	the base AI Bot row.
	"""
	levels = {0}
	for row in frappe.db.sql(
		"SELECT DISTINCT permlevel FROM `tabDocField` WHERE parent=%s",
		(doctype,),
	):
		levels.add(int(row[0] or 0))
	for row in frappe.db.sql(
		"SELECT DISTINCT permlevel FROM `tabCustom Field` WHERE dt=%s",
		(doctype,),
	):
		levels.add(int(row[0] or 0))
	return levels


def _insert_ai_bot_row(table, doctype, permlevel):
	"""Add one AI Bot row at the given permlevel into `table` (DocPerm or
	Custom DocPerm). Both doctypes have identical schemas, so one helper covers
	both cases — only the parent doctype name differs.
	"""
	doc = frappe.new_doc(table)
	doc.parent = doctype
	doc.parenttype = "DocType"
	doc.parentfield = "permissions"
	doc.role = ROLE_NAME
	doc.permlevel = permlevel
	for ptype in _AI_BOT_PTYPES:
		if hasattr(doc, ptype):
			setattr(doc, ptype, 1)
	doc.db_insert()


# ---------------- Report + Page perms ----------------------------------------


def setup_report_permissions():
	"""Add AI Bot to the Has Role table of every role-restricted Report.

	Reports with no roles listed are open to everyone, so we skip those.
	Reports that restrict by role would otherwise lock AI Bot out even though
	the doctype-level row gives it `report` access.
	"""
	_add_role_to_has_role(parenttype="Report")


def setup_page_permissions():
	"""Add AI Bot to the Has Role table of every role-restricted Page.

	Same shape as reports — Page.is_permitted returns True when no roles are
	listed, so only restricted pages need the entry.
	"""
	_add_role_to_has_role(parenttype="Page")


def _add_role_to_has_role(parenttype):
	"""Ensure AI Bot is in Has Role for every restricted parent of `parenttype`.

	Shared between Report and Page since their access pattern is identical.
	"""
	existing = set(
		frappe.get_all(
			"Has Role",
			filters={"parenttype": parenttype, "role": ROLE_NAME},
			pluck="parent",
		)
	)
	restricted = set(
		frappe.get_all(
			"Has Role",
			filters={"parenttype": parenttype},
			fields=["parent"],
			group_by="parent",
			pluck="parent",
		)
	)
	missing = restricted - existing

	count = 0
	for parent in missing:
		doc = frappe.new_doc("Has Role")
		doc.parent = parent
		doc.parenttype = parenttype
		doc.parentfield = "roles"
		doc.role = ROLE_NAME
		doc.db_insert()
		count += 1
		if count % BATCH_SIZE == 0:
			frappe.db.commit()
	if count:
		frappe.db.commit()

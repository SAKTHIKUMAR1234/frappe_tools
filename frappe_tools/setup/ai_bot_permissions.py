import frappe


ROLE_NAME = "AI Bot"
SETTINGS_DOCTYPE = "AI Bot Settings"
BATCH_SIZE = 200

# Read-style ptypes AI Bot is granted on every (doctype, permlevel) pair.
# Anything not listed here (write, create, delete, submit, cancel, amend,
# share, set_user_permissions) stays at 0 — AI Bot is read-only.
_AI_BOT_PTYPES = ("read", "select", "report", "print", "email", "export")


def setup_ai_bot_permissions():
	"""Re-seed AI Bot permissions every migrate.

	Run via the `after_migrate` hook. Each call:

	  1. Loads the user-curated 'protected doctypes' list from AI Bot Settings.
	     Doctypes in that list are off-limits — neither the wipe nor the
	     re-add touches them, so any manual customization on AI Bot rows for
	     those doctypes survives migrations.
	  2. Wipes every AI Bot row in DocPerm / Custom DocPerm whose `parent` is
	     NOT in the protected list. This drops rows referencing deleted /
	     renamed doctypes (and anything else AI-Bot-related left over from
	     a previous run).
	  3. Wipes every AI Bot row in Has Role for Report / Page parenttype.
	     These get rebuilt below for whichever reports/pages currently exist.
	  4. Adds AI Bot rows back for every current doctype / restricted report /
	     restricted page, at every permlevel the doctype uses.

	Custom DocPerm vs DocPerm: when a doctype already has any Custom DocPerm
	row, AI Bot is added to Custom DocPerm too — otherwise its standard DocPerm
	row would be suppressed by Frappe's wholesale-override rule. Doctypes with
	only standard DocPerm rows get AI Bot in standard DocPerm so we don't
	gratuitously trigger the override.
	"""
	ensure_role_exists()
	protected = _get_protected_doctypes()
	cleanup_ai_bot_rows(protected_doctypes=protected)
	setup_doctype_permissions(protected_doctypes=protected)
	setup_report_permissions()
	setup_page_permissions()
	setup_dashboard_manager_permissions()
	frappe.clear_cache()


# ---------------- Dashboard-manager role (write path) ------------------------
#
# The AI Bot role is READ-ONLY everywhere — it never grants write. The Custom
# User Dashboard feature still needs a bot to CREATE dashboards, so that write
# lives on a SEPARATE role the operator assigns to a dedicated dashboard user
# (typically alongside AI Bot, which supplies the read access). The role is
# owner-scoped (if_owner): a bot creates and edits only its OWN dashboards.

DASHBOARD_MANAGER_ROLE = "Custom User Dashboard Manager"
_DASHBOARD_DOCTYPES = ("Custom User Dashboard", "Custom User Dashboard User")
_MANAGER_PTYPES = ("read", "write", "create", "delete", "report", "print", "email", "export")


def ensure_role_exists():
	"""Create the AI Bot role if it doesn't exist yet."""
	if frappe.db.exists("Role", ROLE_NAME):
		return
	role = frappe.new_doc("Role")
	role.role_name = ROLE_NAME
	role.desk_access = 0
	role.insert(ignore_permissions=True)
	frappe.db.commit()


def ensure_dashboard_manager_role():
	"""Create the Custom User Dashboard Manager role if it doesn't exist yet.
	Needs desk access so an assigned user can reach the dashboard doctype UI."""
	if frappe.db.exists("Role", DASHBOARD_MANAGER_ROLE):
		return
	role = frappe.new_doc("Role")
	role.role_name = DASHBOARD_MANAGER_ROLE
	role.desk_access = 1
	role.insert(ignore_permissions=True)
	frappe.db.commit()


def setup_dashboard_manager_permissions():
	"""Grant the dashboard-manager role owner-scoped create/edit on the Custom
	User Dashboard doctypes. Idempotent: wipes its own rows and re-adds them, so
	re-running migrate keeps exactly one clean row per doctype.
	"""
	ensure_dashboard_manager_role()
	fields = set(_perm_fields())
	for dt in _DASHBOARD_DOCTYPES:
		if not frappe.db.exists("DocType", dt):
			continue
		uses_custom = frappe.db.exists("Custom DocPerm", {"parent": dt})
		target = "Custom DocPerm" if uses_custom else "DocPerm"
		if uses_custom:
			_mirror_standard_into_custom(dt)
		for table in ("DocPerm", "Custom DocPerm"):
			frappe.db.delete(table, {"parent": dt, "role": DASHBOARD_MANAGER_ROLE})
		doc = frappe.new_doc(target)
		doc.parent = dt
		doc.parenttype = "DocType"
		doc.parentfield = "permissions"
		doc.role = DASHBOARD_MANAGER_ROLE
		doc.permlevel = 0
		# set every flag explicitly (DocPerm defaults write-style to 1): grant the
		# manager ptypes, deny submit/cancel/amend/import/share/... then scope to owner.
		for field in fields:
			setattr(doc, field, 1 if field in _MANAGER_PTYPES else 0)
		if "if_owner" in fields:
			doc.if_owner = 1
		doc.db_insert()
	frappe.db.commit()


def _get_protected_doctypes():
	"""Return the set of doctype names whose AI Bot rows must be left alone.

	Defensive about the case where AI Bot Settings hasn't been migrated yet
	(fresh install, first-time bootstrap) — returns an empty set silently
	so the rest of the setup still runs.
	"""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return set()
	try:
		settings = frappe.get_single(SETTINGS_DOCTYPE)
	except Exception:
		return set()
	return {row.doctype_name for row in (settings.protected_doctypes or []) if row.doctype_name}


# ---------------- Cleanup ----------------------------------------------------


def cleanup_ai_bot_rows(protected_doctypes):
	"""Wipe EVERY AI Bot doctype/report permission (except the manual protected
	list), so nothing stale — and no old write-granting row — survives. The
	re-seed then re-adds READ-ONLY on every doctype.

	There is no 'preserve rows that happen to have write' heuristic: that kept
	old full-permission rows (Write/Create/Delete on List View Settings,
	Prospect, …) alive on production. The AI Bot role never grants write; any
	write functionality lives on a SEPARATE role (see
	setup_dashboard_manager_permissions). The protected list is the only thing
	this wipe skips.
	"""
	doctype_filters = {"role": ROLE_NAME}
	if protected_doctypes:
		doctype_filters["parent"] = ("not in", list(protected_doctypes))

	frappe.db.delete("DocPerm", doctype_filters)
	frappe.db.delete("Custom DocPerm", doctype_filters)
	frappe.db.delete(
		"Has Role",
		{"role": ROLE_NAME, "parenttype": ("in", ["Report", "Page"])},
	)
	frappe.db.commit()


def reset_ai_bot_permissions():
	"""Full reset: delete ALL AI Bot doctype/report/page permissions (ignoring
	the protected list too) and re-seed from scratch — READ-ONLY on every
	doctype + report, write nowhere. Run on a site that accumulated stray AI Bot
	write grants (e.g. old full-access rows from a previous scheme):

	    bench --site <site> execute frappe_tools.setup.ai_bot_permissions.reset_ai_bot_permissions
	"""
	frappe.db.delete("DocPerm", {"role": ROLE_NAME})
	frappe.db.delete("Custom DocPerm", {"role": ROLE_NAME})
	frappe.db.delete("Has Role", {"role": ROLE_NAME, "parenttype": ("in", ["Report", "Page"])})
	frappe.db.commit()
	setup_ai_bot_permissions()
	print("AI Bot permissions reset: READ-ONLY on every DocType + Report; write nowhere. "
		"Dashboard writes are on the separate '{0}' role.".format(DASHBOARD_MANAGER_ROLE))


# ---------------- DocType perms ----------------------------------------------


def setup_doctype_permissions(protected_doctypes):
	"""Add AI Bot read rows to every DocType at every permlevel it uses.

	For each doctype (skipping those in `protected_doctypes`):
	  1. Pick the target table — Custom DocPerm if the doctype already has any
	     Custom DocPerm rows, otherwise standard DocPerm.
	  2. Collect every distinct permlevel present on the doctype's fields plus
	     Custom Fields (always including 0).
	  3. Insert one AI Bot row per permlevel.

	After `cleanup_ai_bot_rows()` has run, there are no AI Bot rows on any
	non-protected doctype, so the inner existence-check is mainly belt-and-
	braces for re-entrancy.
	"""
	doctypes = frappe.get_all("DocType", pluck="name")

	doctypes_with_custom = set(
		frappe.get_all("Custom DocPerm", pluck="parent", distinct=True)
	)
	# key on rows that GRANT READ (not merely exist): a preserved write-only
	# user row must NOT block our read grant — we ensure read on every doctype.
	existing_in_custom = _existing_ai_bot_read_rows("Custom DocPerm")
	existing_in_standard = _existing_ai_bot_read_rows("DocPerm")

	added = 0
	for doctype in doctypes:
		if doctype in protected_doctypes:
			continue

		uses_custom = doctype in doctypes_with_custom
		target = "Custom DocPerm" if uses_custom else "DocPerm"
		existing = existing_in_custom if uses_custom else existing_in_standard

		# Custom DocPerm wholesale-overrides standard DocPerm: once a doctype has
		# any Custom DocPerm row, Frappe ignores its standard DocPerm entirely.
		# Before adding AI Bot to Custom DocPerm we therefore mirror every
		# standard DocPerm row that isn't already there, so no other role silently
		# loses access (this is what Frappe's own setup_custom_perms does upfront).
		# Without it, adding AI Bot to a doctype whose Custom DocPerm is an
		# incomplete mirror compounds the 2026-05-29 lockout.
		if uses_custom:
			_mirror_standard_into_custom(doctype)

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


# Candidate permission flag columns; the live set is resolved against the
# actual schema (varies by Frappe version) so we never query/copy a column
# that doesn't exist on this site.
_CANDIDATE_PERM_FIELDS = (
	"select", "read", "write", "create", "delete", "submit", "cancel",
	"amend", "report", "export", "import", "print", "email", "share",
	"set_user_permissions", "if_owner",
	# v16 adds a field-level masking column; intersected with the live schema in
	# _perm_fields() so it's simply ignored on v15 (column absent).
	"mask",
)


def _perm_fields():
	"""Permission columns present in both DocPerm and Custom DocPerm here."""
	shared = set(frappe.db.get_table_columns("DocPerm")) & set(
		frappe.db.get_table_columns("Custom DocPerm")
	)
	return [f for f in _CANDIDATE_PERM_FIELDS if f in shared]


def _mirror_standard_into_custom(doctype):
	"""Copy any standard DocPerm (role, permlevel) row missing from this
	doctype's Custom DocPerm, so its override set is a complete mirror.

	Additive only — never edits or deletes an existing Custom DocPerm row, so
	deliberate customizations survive. AI Bot is skipped here; it's added
	separately by the caller at the permlevels it needs.
	"""
	present = {
		(r.role, int(r.permlevel or 0))
		for r in frappe.get_all(
			"Custom DocPerm",
			filters={"parent": doctype},
			fields=["role", "permlevel"],
		)
	}
	perm_fields = _perm_fields()
	std_rows = frappe.get_all(
		"DocPerm",
		filters={"parent": doctype},
		fields=["role", "permlevel", *perm_fields],
	)
	for row in std_rows:
		if row.role == ROLE_NAME:
			continue
		if (row.role, int(row.permlevel or 0)) in present:
			continue
		doc = frappe.new_doc("Custom DocPerm")
		doc.parent = doctype
		doc.parenttype = "DocType"
		doc.parentfield = "permissions"
		doc.role = row.role
		doc.permlevel = int(row.permlevel or 0)
		for ptype in perm_fields:
			if hasattr(doc, ptype):
				setattr(doc, ptype, row.get(ptype) or 0)
		doc.db_insert()


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


def _existing_ai_bot_read_rows(table):
	"""Set of (parent, permlevel) where an AI Bot row GRANTS read in `table`."""
	return {
		(row.parent, int(row.permlevel or 0))
		for row in frappe.get_all(
			table,
			filters={"role": ROLE_NAME, "read": 1},
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
	"""Add one READ-ONLY AI Bot row at the given permlevel into `table` (DocPerm
	or Custom DocPerm).

	CRITICAL: `frappe.new_doc("DocPerm")` defaults write/create/delete/share to 1
	(standard DocPerm's "full access" defaults — Custom DocPerm defaults them to
	0). So we must set EVERY permission flag explicitly: read-style → 1, all
	write-style / if_owner → 0. Relying on the defaults silently granted AI Bot
	full write on every standard-DocPerm doctype (the 2026-07 prod bug).
	"""
	doc = frappe.new_doc(table)
	doc.parent = doctype
	doc.parenttype = "DocType"
	doc.parentfield = "permissions"
	doc.role = ROLE_NAME
	doc.permlevel = permlevel
	for field in _perm_fields():
		setattr(doc, field, 1 if field in _AI_BOT_PTYPES else 0)
	doc.db_insert()


# ---------------- Report + Page perms ----------------------------------------


def setup_report_permissions():
	"""Add AI Bot to the Has Role table of every role-restricted Report."""
	_add_role_to_has_role(parenttype="Report")


def setup_page_permissions():
	"""Add AI Bot to the Has Role table of every role-restricted Page."""
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

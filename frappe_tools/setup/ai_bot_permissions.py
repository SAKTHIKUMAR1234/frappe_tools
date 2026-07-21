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
	allowlist = _get_write_allowed_doctypes()
	cleanup_ai_bot_rows(protected_doctypes=protected, allowlist=allowlist)
	setup_doctype_permissions(protected_doctypes=protected)
	setup_write_permissions(protected_doctypes=protected)
	setup_report_permissions()
	setup_page_permissions()
	frappe.clear_cache()


# ptypes that mean "a user deliberately granted more than our read-only default"
_WRITE_LIKE = ("write", "create", "delete", "submit", "cancel", "amend",
	"import", "share", "set_user_permissions")


def _is_our_default_row(row, fields):
	"""True if this AI Bot row is one WE plant: pure read-style, no if_owner.
	Any write-style flag or if_owner means a USER customized it → we preserve it."""
	if int(row.get("if_owner") or 0):
		return False
	return not any(int(row.get(p) or 0) for p in _WRITE_LIKE if p in fields)


# DocTypes the AI Bot may WRITE (create/write/delete) — its own dashboard feature
# by default, plus anything the operator adds in AI Bot Settings. Everything else
# stays read-only. The dashboard doctypes are owner-scoped: a bot edits only its own.
_DEFAULT_WRITABLE = ("Custom User Dashboard", "Custom User Dashboard User")
_OWNER_SCOPED = {"Custom User Dashboard", "Custom User Dashboard User"}
_WRITE_GRANT_PTYPES = ("write", "create", "delete")


def _get_write_allowed_doctypes():
	allowed = set(_DEFAULT_WRITABLE)
	if frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		try:
			settings = frappe.get_single(SETTINGS_DOCTYPE)
			allowed |= {row.doctype_name
				for row in (settings.get("write_allowed_doctypes") or []) if row.doctype_name}
		except Exception:
			pass
	return allowed


def setup_write_permissions(protected_doctypes):
	"""Grant AI Bot WRITE on each write-allowed DocType, WITHOUT touching its
	read-everywhere row.

	- Owner-scoped doctypes (the dashboard): the base read row planted by
	  setup_doctype_permissions stays as read-ALL (so 'read everything' holds),
	  and a SEPARATE if_owner row adds write/create/delete — a bot reads every
	  dashboard but edits only its own.
	- Other write-allowed doctypes: write/create/delete are added onto the
	  existing (read-all) row (write any).
	"""
	fields = set(_perm_fields())
	for dt in _get_write_allowed_doctypes():
		if dt in protected_doctypes or not frappe.db.exists("DocType", dt):
			continue
		for table in ("Custom DocPerm", "DocPerm"):
			rows = frappe.get_all(table,
				filters={"parent": dt, "role": ROLE_NAME, "permlevel": 0}, pluck="name")
			if not rows:
				continue
			if dt in _OWNER_SCOPED:
				# keep read-all row; add a separate if_owner write row (once)
				if not frappe.db.exists(table,
					{"parent": dt, "role": ROLE_NAME, "permlevel": 0, "if_owner": 1}):
					doc = frappe.new_doc(table)
					doc.parent = dt
					doc.parenttype = "DocType"
					doc.parentfield = "permissions"
					doc.role = ROLE_NAME
					doc.permlevel = 0
					for p in _WRITE_GRANT_PTYPES:
						if p in fields:
							setattr(doc, p, 1)
					if "if_owner" in fields:
						doc.if_owner = 1
					doc.db_insert()
			else:
				vals = {p: 1 for p in _WRITE_GRANT_PTYPES if p in fields}
				for name in rows:
					frappe.db.set_value(table, name, vals, update_modified=False)
	frappe.db.commit()


def ensure_role_exists():
	"""Create the AI Bot role if it doesn't exist yet."""
	if frappe.db.exists("Role", ROLE_NAME):
		return
	role = frappe.new_doc("Role")
	role.role_name = ROLE_NAME
	role.desk_access = 0
	role.insert(ignore_permissions=True)
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


def cleanup_ai_bot_rows(protected_doctypes, allowlist):
	"""Drop only the AI Bot rows WE manage; PRESERVE what a user granted.

	Deleted (ours / stale, to be re-seeded fresh):
	  - rows on a DocType that no longer exists (stale),
	  - rows on our write-allowlist doctypes (we own those grants),
	  - our default read-only rows (pure read-style, no if_owner).
	Preserved (never touched):
	  - rows the manual protected list names,
	  - rows a USER granted on any other app's DocType — i.e. rows carrying a
	    write-style flag or if_owner. E.g. a hand-added AI Bot write on
	    'Packing Slip Item' stays exactly as the user set it.

	Has Role (Report/Page) is wiped and rebuilt as before.
	"""
	fields = _perm_fields()
	all_doctypes = set(frappe.get_all("DocType", pluck="name"))
	for table in ("DocPerm", "Custom DocPerm"):
		rows = frappe.get_all(
			table, filters={"role": ROLE_NAME},
			fields=["name", "parent", *fields],
		)
		to_delete = []
		for r in rows:
			dt = r.parent
			if dt in protected_doctypes:
				continue  # manual protection — leave alone
			if dt not in all_doctypes:
				to_delete.append(r.name)  # stale (deleted/renamed doctype)
			elif dt in allowlist:
				to_delete.append(r.name)  # our managed write target — re-seeded
			elif _is_our_default_row(r, fields):
				to_delete.append(r.name)  # our default read row — re-seeded
			# else: user-given write/if_owner on another app's doctype → KEEP
		if to_delete:
			frappe.db.delete(table, {"name": ("in", to_delete)})

	frappe.db.delete(
		"Has Role",
		{"role": ROLE_NAME, "parenttype": ("in", ["Report", "Page"])},
	)
	frappe.db.commit()


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
	"""Add one AI Bot row at the given permlevel into `table` (DocPerm or
	Custom DocPerm). Both have identical schemas, so one helper covers both.
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

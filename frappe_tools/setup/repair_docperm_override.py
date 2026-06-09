"""Repair doctypes left in broken Custom DocPerm override mode.

Frappe rule: if a doctype has ANY Custom DocPerm row, the framework ignores
ALL standard DocPerm rows for that doctype — Custom DocPerm wholesale-replaces
them. Frappe's own `setup_custom_perms` therefore mirrors every standard
DocPerm row into Custom DocPerm BEFORE applying any edit, so no role silently
loses access.

The old AI Bot seeding inserted Custom DocPerm rows WITHOUT that mirror, which
flipped doctypes into override with an incomplete role set — dropping roles
(incl. System Manager / All) that the shipped defaults grant. This module
completes the mirror non-destructively:

  * For every doctype already in override (has >=1 Custom DocPerm row), it
    re-inserts any standard DocPerm (role, permlevel) pair that is MISSING from
    Custom DocPerm, copying the standard row's permission flags verbatim.
  * It NEVER deletes or edits an existing Custom DocPerm row, so deliberate
    Essdee customizations already in Custom DocPerm are preserved untouched.

Run a dry run first:
    bench --site <site> execute \
        frappe_tools.setup.repair_docperm_override.dry_run

Then apply:
    bench --site <site> execute \
        frappe_tools.setup.repair_docperm_override.apply
"""

import frappe


# AI Bot is deliberately NOT restored into Custom DocPerm. It's a bot role, so
# its absence costs no human any access, and programmatically writing AI Bot
# Custom DocPerm rows is the exact behaviour that caused the 2026-05-29 outage.
# The bot's read access is handled separately by the (corrected) seeding logic.
_SKIP_ROLES = {"AI Bot"}

# Candidate permission flag columns. The actual set is resolved at runtime
# against the live schema (it varies by Frappe version — e.g. some versions
# have no `set_user_permissions` column), so we only ever query/copy columns
# that exist in BOTH DocPerm and Custom DocPerm.
_CANDIDATE_PTYPE_FIELDS = (
	"select", "read", "write", "create", "delete", "submit", "cancel",
	"amend", "report", "export", "import", "print", "email", "share",
	"set_user_permissions", "if_owner",
)


def _ptype_fields():
	"""Permission columns present in both DocPerm and Custom DocPerm on this
	site, preserving the canonical order above."""
	shared = set(frappe.db.get_table_columns("DocPerm")) & set(
		frappe.db.get_table_columns("Custom DocPerm")
	)
	return [f for f in _CANDIDATE_PTYPE_FIELDS if f in shared]


def _plan():
	"""Return a list of standard DocPerm rows that are missing from Custom
	DocPerm on doctypes already in override mode.

	Each entry is the standard DocPerm row dict (incl. name + flags); the caller
	either prints it (dry run) or mirrors it into Custom DocPerm (apply).
	"""
	override_doctypes = frappe.get_all(
		"Custom DocPerm", pluck="parent", distinct=True
	)
	if not override_doctypes:
		return []

	# (parent, role, permlevel) tuples that already exist in Custom DocPerm.
	existing_custom = {
		(r.parent, r.role, int(r.permlevel or 0))
		for r in frappe.get_all(
			"Custom DocPerm",
			filters={"parent": ("in", override_doctypes)},
			fields=["parent", "role", "permlevel"],
		)
	}

	std_rows = frappe.get_all(
		"DocPerm",
		filters={"parent": ("in", override_doctypes)},
		fields=["name", "parent", "role", "permlevel", *_ptype_fields()],
	)

	missing = []
	for row in std_rows:
		if row.role in _SKIP_ROLES:
			continue
		key = (row.parent, row.role, int(row.permlevel or 0))
		if key not in existing_custom:
			missing.append(row)
	missing.sort(key=lambda r: (r.parent, int(r.permlevel or 0), r.role))
	return missing


def dry_run():
	"""Print every (doctype, role, permlevel) that would be restored. No writes."""
	missing = _plan()
	if not missing:
		print("Nothing to repair — every override doctype already mirrors its "
			  "standard DocPerm.")
		return

	ptype_fields = _ptype_fields()
	per_doctype = {}
	for row in missing:
		per_doctype.setdefault(row.parent, []).append(row)

	print(f"Would restore {len(missing)} row(s) across "
		  f"{len(per_doctype)} doctype(s):\n")
	for doctype in sorted(per_doctype):
		print(f"  {doctype}")
		for row in per_doctype[doctype]:
			flags = ",".join(f for f in ptype_fields if row.get(f))
			print(f"      + {row.role} (permlevel {int(row.permlevel or 0)}): "
				  f"{flags or '(no flags set)'}")
	print("\nDry run only — no changes written. Run `apply` to perform the "
		  "restore.")


def apply():
	"""Mirror missing standard DocPerm rows into Custom DocPerm. Additive only."""
	missing = _plan()
	if not missing:
		print("Nothing to repair.")
		return

	ptype_fields = _ptype_fields()
	for row in missing:
		doc = frappe.new_doc("Custom DocPerm")
		doc.parent = row.parent
		doc.parenttype = "DocType"
		doc.parentfield = "permissions"
		doc.role = row.role
		doc.permlevel = int(row.permlevel or 0)
		for ptype in ptype_fields:
			if hasattr(doc, ptype):
				setattr(doc, ptype, row.get(ptype) or 0)
		doc.db_insert()

	frappe.db.commit()
	frappe.clear_cache()
	print(f"Restored {len(missing)} Custom DocPerm row(s). "
		  f"Run a dry run again to confirm it now reports nothing to repair.")

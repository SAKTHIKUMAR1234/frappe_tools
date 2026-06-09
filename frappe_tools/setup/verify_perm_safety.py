"""Prove that running the AI Bot permission seeding does NOT strip any other
role's permissions — i.e. that installing/migrating frappe_tools is safe.

The 2026-05-29 outage happened because seeding wrote Custom DocPerm rows that
flipped doctypes into wholesale-override, silently dropping write/create/delete
for other roles (the Permission Manager still showed them, but enforcement
denied — exactly the reported symptom). This verifier guards against any
regression of that kind.

What it does, in ONE process (so the before-snapshot is real):

  1. Snapshot the EFFECTIVE doctype permission of every non-AI-Bot role.
     "Effective" mirrors Frappe enforcement: a doctype with any Custom DocPerm
     row is governed by Custom DocPerm; otherwise by standard DocPerm.
  2. Run `setup_ai_bot_permissions()` — the exact action an `after_migrate`
     would perform.
  3. Re-snapshot and diff. Any (role, doctype, permlevel, ptype) that was
     granted before and is now denied is a REGRESSION → the seeding is unsafe.

Run on a throwaway / prod-COPY site (it mutates permissions):
    bench --site <copy> execute frappe_tools.setup.verify_perm_safety.verify
"""

import frappe

from frappe_tools.setup.ai_bot_permissions import setup_ai_bot_permissions
from frappe_tools.setup.repair_docperm_override import _ptype_fields


ROLE = "AI Bot"


def _effective_perms():
	"""Map every granted (doctype, role, permlevel, ptype) for non-AI-Bot roles,
	using the same Custom-DocPerm-overrides-DocPerm rule Frappe enforces with."""
	ptypes = _ptype_fields()
	custom_parents = set(
		frappe.get_all("Custom DocPerm", pluck="parent", distinct=True)
	)

	granted = set()
	for table in ("DocPerm", "Custom DocPerm"):
		rows = frappe.get_all(
			table, fields=["parent", "role", "permlevel", *ptypes]
		)
		for row in rows:
			if row.role == ROLE:
				continue
			in_custom = row.parent in custom_parents
			# A standard DocPerm row is inert once the doctype has any Custom
			# DocPerm row, so it doesn't count toward effective access.
			if table == "DocPerm" and in_custom:
				continue
			for p in ptypes:
				if row.get(p):
					granted.add((row.parent, row.role, int(row.permlevel or 0), p))
	return granted


def verify():
	before = _effective_perms()

	setup_ai_bot_permissions()
	frappe.clear_cache()

	after = _effective_perms()

	lost = sorted(before - after)
	if not lost:
		print(f"PASS — migrate seeding caused ZERO permission regressions.\n"
			  f"Checked {len(before)} granted (role, doctype, permlevel, ptype) "
			  f"tuples across all non-AI-Bot roles; all still granted after "
			  f"seeding.")
		return

	print(f"FAIL — {len(lost)} permission(s) were REVOKED by seeding:\n")
	for parent, role, permlevel, ptype in lost:
		print(f"  {role}  lost  {ptype}  on  {parent} (permlevel {permlevel})")

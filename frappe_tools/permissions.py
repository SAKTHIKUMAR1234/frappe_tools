"""AI Bot read access — additive permission hooks.

Doctype-level + field-level (permlevel) read access is granted by Custom
DocPerm rows planted by `setup/ai_bot_permissions.py`. Those rows survive
'Restore Original Permissions' and bench migrate, so the runtime hooks here
are just two thin safety nets:

  - `ai_bot_has_permission` — wired via the `has_permission` hook in hooks.py.
    Fires only after the normal DocPerm check passes, so it can't grant access
    on its own; it's a belt-and-braces yes for document-level callers that
    pass a `doc` argument and might want a definite True for read-style ptypes.
  - `ai_bot_query_conditions` — wired via the `permission_query_conditions`
    hook in hooks.py. Returns an empty WHERE clause for AI Bot so list/report
    SQL is not restricted by User Permission filters — AI Bot reads every row.
"""

import frappe


ROLE = "AI Bot"
ALLOWED_PTYPES = {"read", "select", "report", "export", "print", "email"}


def ai_bot_has_permission(doc, ptype="read", user=None):
	"""Additive has_permission hook — return True for read-style ptypes when
	the user holds AI Bot. Returns None for everything else so other hooks /
	the standard check stay in charge.
	"""
	if ptype not in ALLOWED_PTYPES:
		return None
	user = user or frappe.session.user
	if ROLE in frappe.get_roles(user):
		return True
	return None


def ai_bot_query_conditions(user=None, doctype=None):
	"""Strip row-level filters from list/report SQL for AI Bot.

	An empty string means 'no extra WHERE clause' — AI Bot sees every row of
	every doctype regardless of User Permissions. Returning None for other
	users keeps Frappe's standard filtering intact.
	"""
	user = user or frappe.session.user
	if ROLE in frappe.get_roles(user):
		return ""
	return None

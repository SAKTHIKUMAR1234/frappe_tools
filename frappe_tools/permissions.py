import frappe


ROLE = "AI Bot"
ALLOWED_PTYPES = {"read", "select", "report", "export", "print"}


def ai_bot_has_permission(doc, ptype="read", user=None):
	"""Grant AI Bot universal read-style access without touching DocPerm.

	Returning True short-circuits the standard permission check; returning None
	means "no opinion — fall through to standard checks", so non-AI-Bot users
	and write/create/delete attempts are unaffected.
	"""
	if ptype not in ALLOWED_PTYPES:
		return None
	user = user or frappe.session.user
	if ROLE in frappe.get_roles(user):
		return True
	return None


def ai_bot_query_conditions(user=None, doctype=None):
	"""Remove row-level filters for AI Bot in list/report queries.

	An empty string means "no extra WHERE clause" — i.e. allow all rows.
	Returning None lets standard query conditions apply for other users.
	"""
	user = user or frappe.session.user
	if ROLE in frappe.get_roles(user):
		return ""
	return None

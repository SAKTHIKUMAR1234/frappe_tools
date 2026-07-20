"""AI Bot access — additive permission hooks.

Doctype-level + field-level (permlevel) read access is granted by Custom DocPerm
rows planted by `setup/ai_bot_permissions.py`. Those rows survive 'Restore
Original Permissions' and bench migrate, so the runtime hooks here are thin,
additive nets.

`ai_bot_has_permission` is wired as a wildcard "*" `has_permission` hook. The two
Frappe versions invert the controller-hook contract, so it MUST behave
differently per version:

  - v15: `has_controller_permissions` runs the "*" hooks FIRST (reversed order)
    and the first non-None return short-circuits the whole check. So the wildcard
    must stay NEUTRAL (return None) for everyone except a deliberate belt-and-
    braces True for AI Bot reads. Returning True for everyone would bypass every
    other app's deny hook (a security hole).
  - v16: a controller hook can ONLY deny — any falsy return (None/False/'') is a
    hard DENY, truthy just continues. So it must never return falsy: return True
    (a non-denying no-op). AI Bot's real grant still comes from the DocPerm rows.

`ai_bot_query_conditions` (the `permission_query_conditions` hook) is unaffected
by the version change and is identical on both.
"""

import frappe


ROLE = "AI Bot"
ALLOWED_PTYPES = {"read", "select", "report", "export", "print", "email"}


def _is_v16():
	"""True on Frappe v16+ where controller permission hooks deny on a falsy return."""
	try:
		return int(frappe.__version__.split(".")[0]) >= 16
	except Exception:
		return False


def ai_bot_has_permission(doc, ptype="read", user=None):
	"""Additive, version-aware has_permission hook. See module docstring."""
	if _is_v16():
		# v16: never return falsy (it would DENY); a wildcard hook can only decline
		# to deny, so True is the safe neutral. The grant comes from DocPerm rows.
		return True
	# v15: stay neutral (None) so other apps' controller deny hooks still run;
	# only a belt-and-braces True for AI Bot on read-style ptypes.
	if ptype not in ALLOWED_PTYPES:
		return None
	if ROLE in frappe.get_roles(user or frappe.session.user):
		return True
	return None


def ai_bot_query_conditions(user=None, doctype=None):
	"""Strip row-level filters from list/report SQL for AI Bot.

	An empty string means 'no extra WHERE clause' — AI Bot sees every row of every
	doctype regardless of User Permissions. Returning None for other users keeps
	Frappe's standard filtering intact.
	"""
	user = user or frappe.session.user
	if ROLE in frappe.get_roles(user):
		return ""
	return None

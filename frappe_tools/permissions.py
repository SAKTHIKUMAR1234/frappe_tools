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
# Anything that mutates data or grants access. The AI Bot role grants NONE of
# these anywhere (it is read-only), and is hard-DENIED them on the escalation
# surface below no matter what — see ESCALATION_DOCTYPES.
WRITE_PTYPES = {"write", "create", "delete", "submit", "cancel", "amend",
	"import", "share", "set_user_permissions"}

# The privilege-escalation surface: doctypes that grant roles/permissions, run
# code, or change global config. An AI Bot user is BLOCKED from writing these no
# matter what — this closes Frappe built-in bypasses (e.g. a user editing its OWN
# User doc to add itself a role, which slips past has_permission).
#
# Everything else is governed purely by DocPerm. AI Bot's rows are read-only, so
# an AI-Bot-ONLY user can't write anything. If the operator wants a bot to write
# a specific doctype (e.g. create dashboards), they grant a SEPARATE role for it;
# that role's write is honoured here (not on this list) — AI Bot never grants it.
ESCALATION_DOCTYPES = {
	"User", "Role", "Custom Role", "Role Profile", "Has Role",
	"DocPerm", "Custom DocPerm", "DocShare", "User Permission",
	"Property Setter", "Custom Field", "Client Script", "Server Script",
	"System Settings",
}


def _is_v16():
	"""True on Frappe v16+ where controller permission hooks deny on a falsy return."""
	try:
		return int(frappe.__version__.split(".")[0]) >= 16
	except Exception:
		return False


def ai_bot_has_permission(doc, ptype="read", user=None):
	"""Additive, version-aware has_permission hook. See module docstring.

	Hard WRITE DENY: for the AI Bot role, any mutating ptype on an
	escalation-surface DocType is denied outright — independent of whatever
	DocPerm rows or other roles exist. Non-escalation writes are left to DocPerm
	(AI Bot has no write rows; a separate role may grant them). System Managers
	are never restricted."""
	user = user or frappe.session.user
	roles = frappe.get_roles(user)
	is_ai_bot = ROLE in roles and "System Manager" not in roles
	dt = getattr(doc, "doctype", None)

	if is_ai_bot and ptype in WRITE_PTYPES and dt in ESCALATION_DOCTYPES:
		return False  # DENY: AI Bot may never write the escalation surface

	if _is_v16():
		# v16: never return falsy (it would DENY); True is the safe neutral for
		# reads / non-escalation writes. Real grants come from DocPerm rows.
		return True
	# v15: first non-None return short-circuits the whole check.
	# - AI Bot read-style ptypes → True (guarantee read-everything).
	# - everything else → None: stay neutral so DocPerm / other roles decide
	#   (a non-escalation write is allowed ONLY if some other role's DocPerm
	#   grants it — returning None never bypasses that check).
	if is_ai_bot and ptype in ALLOWED_PTYPES:
		return True
	return None


def ai_bot_guard_write(doc, method=None):
	"""Hard write guard wired as a wildcard doc_event (before_insert/before_save/
	on_trash). Throws for the AI Bot role on the escalation surface — the
	enforcement of record, independent of has_permission hook ordering (Frappe's
	built-in User self-edit, for instance, slips past the permission hook and
	would otherwise let an AI Bot add itself to a role).

	Non-escalation doctypes are not guarded here: an AI-Bot-only user is already
	blocked by DocPerm (no write rows), and a user who ALSO holds a separate
	write-granting role must be allowed to use it. System Managers are never
	restricted; trusted server writes that set ignore_permissions are exempt."""
	roles = frappe.get_roles(frappe.session.user)
	if ROLE not in roles or "System Manager" in roles:
		return
	if getattr(getattr(doc, "flags", None), "ignore_permissions", False):
		return
	if getattr(doc, "doctype", None) not in ESCALATION_DOCTYPES:
		return
	frappe.throw(
		frappe._("AI Bot is not permitted to write {0}").format(getattr(doc, "doctype", "")),
		frappe.PermissionError,
	)


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

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
# Anything that mutates data or grants access. AI Bot is DENIED these on every
# doctype except the configured write-allowlist (below) — a hard runtime guard
# on top of the DocPerm rows, so no stray/injected write row can be exercised.
WRITE_PTYPES = {"write", "create", "delete", "submit", "cancel", "amend",
	"import", "share", "set_user_permissions"}

# DocTypes the AI Bot may always write, even before AI Bot Settings is
# configured — its own page feature. Extra ones are added via the settings table.
DEFAULT_WRITABLE = {"AI Bot Page", "AI Bot Page User"}


def _is_v16():
	"""True on Frappe v16+ where controller permission hooks deny on a falsy return."""
	try:
		return int(frappe.__version__.split(".")[0]) >= 16
	except Exception:
		return False


def _write_allowed_doctypes():
	"""The set of DocTypes AI Bot may write (settings table + defaults),
	memoized per request."""
	cached = getattr(frappe.local, "_ai_bot_write_allow", None)
	if cached is not None:
		return cached
	allowed = set(DEFAULT_WRITABLE)
	try:
		if frappe.db.exists("DocType", "AI Bot Settings"):
			settings = frappe.get_cached_doc("AI Bot Settings")
			allowed |= {r.doctype_name for r in (settings.get("write_allowed_doctypes") or [])
				if r.doctype_name}
	except Exception:
		pass
	frappe.local._ai_bot_write_allow = allowed
	return allowed


def ai_bot_has_permission(doc, ptype="read", user=None):
	"""Additive, version-aware has_permission hook. See module docstring.

	Adds a hard WRITE DENY: for the AI Bot role, any mutating ptype on a
	DocType NOT in the write-allowlist is denied outright — independent of
	whatever DocPerm rows exist. System Managers are never restricted."""
	user = user or frappe.session.user
	roles = frappe.get_roles(user)
	is_ai_bot = ROLE in roles and "System Manager" not in roles

	if is_ai_bot and ptype in WRITE_PTYPES:
		dt = getattr(doc, "doctype", None)
		if dt and dt in _write_allowed_doctypes():
			# allowed target → don't deny; the DocPerm rows grant it
			return True if _is_v16() else None
		return False  # DENY: AI Bot may not write this doctype

	if _is_v16():
		# v16: never return falsy (it would DENY); True is the safe neutral for
		# reads / non-AI-Bot. The read grant comes from DocPerm rows.
		return True
	# v15: stay neutral (None) so other apps' controller deny hooks still run;
	# only a belt-and-braces True for AI Bot on read-style ptypes.
	if ptype not in ALLOWED_PTYPES:
		return None
	if is_ai_bot:
		return True
	return None


def ai_bot_guard_write(doc, method=None):
	"""Hard write guard wired as a wildcard doc_event (before_insert/before_save/
	on_trash). Throws for the AI Bot role on any DocType NOT in the write-
	allowlist — the enforcement of record, independent of has_permission hook
	ordering (Frappe's built-in User self-edit, for instance, slips past the
	permission hook and would otherwise let an AI Bot add itself to a role).

	System Managers are never restricted; trusted server writes that set
	ignore_permissions are exempt (they run deliberately, not as the API user)."""
	roles = frappe.get_roles(frappe.session.user)
	if ROLE not in roles or "System Manager" in roles:
		return
	if getattr(getattr(doc, "flags", None), "ignore_permissions", False):
		return
	if getattr(doc, "doctype", None) in _write_allowed_doctypes():
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

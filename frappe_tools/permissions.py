"""AI Bot universal read access — implemented via monkey-patch.

Frappe's `has_permission` hook is *additive*: it only runs after the role-based
DocPerm check passes. Since the AI Bot role has no DocPerm rows (and we don't
want any, because they get wiped by 'Restore Original Permissions' and bench
migrate), the hook approach alone is insufficient.

Instead, we monkey-patch `frappe.permissions.has_permission` so that any user
holding the AI Bot role short-circuits to True for read-style ptypes. The patch
is installed lazily on every request via the `before_request` hook, which makes
it survive worker restarts and module reloads without depending on app import
order.

For list/report queries, we also expose `ai_bot_query_conditions` which is
wired up via the `permission_query_conditions` hook in hooks.py — that one is
called by `frappe.db.get_list` and friends regardless of DocPerm state.
"""

import frappe


ROLE = "AI Bot"
ALLOWED_PTYPES = {"read", "select", "report", "export", "print"}

_PATCH_INSTALLED = False


_FULL_READ_ROLE_PERMS = {
	"read": 1,
	"select": 1,
	"report": 1,
	"print": 1,
	"email": 1,
	"export": 1,
	"write": 0,
	"create": 0,
	"delete": 0,
	"submit": 0,
	"cancel": 0,
	"amend": 0,
	"import": 0,
	"share": 0,
	"if_owner": {},
	"has_if_owner_enabled": False,
}


def _is_ai_bot(user):
	"""Return True if the given user holds the AI Bot role."""
	if not user or user == "Guest":
		return False
	try:
		return ROLE in frappe.get_roles(user)
	except Exception:
		return False


def install_permission_patch():
	"""Replace Frappe's permission entry points with AI-Bot-aware versions.

	Patches both `frappe.permissions.has_permission` and
	`frappe.permissions.get_role_permissions`. The latter is critical because
	`DatabaseQuery.build_match_conditions` calls it directly (db_query.py:995)
	and would otherwise throw "No permission to read X" before any hook fires.

	Idempotent: only patches once per Python process. Uses *args/**kwargs to
	stay compatible across Frappe versions whose signatures differ slightly.
	"""
	global _PATCH_INSTALLED
	if _PATCH_INSTALLED:
		return

	import frappe.permissions as fp

	# ----- Patch has_permission ------------------------------------------------
	original_has_permission = fp.has_permission

	def patched_has_permission(doctype, ptype="read", *args, **kwargs):
		# Resolve user from kwargs, positional args, or session.
		user = kwargs.get("user")
		if user is None and len(args) >= 3:
			# Positional order in upstream: (doc, verbose, user, ...)
			user = args[2]
		if user is None:
			user = getattr(frappe.session, "user", None)

		if (
			isinstance(doctype, str)
			and ptype in ALLOWED_PTYPES
			and _is_ai_bot(user)
		):
			return True

		return original_has_permission(doctype, ptype, *args, **kwargs)

	fp.has_permission = patched_has_permission
	# Some call sites use frappe.has_permission directly — patch that too.
	frappe.has_permission = patched_has_permission

	# ----- Patch get_role_permissions -----------------------------------------
	# DatabaseQuery.build_match_conditions calls this directly and bypasses
	# has_permission entirely. Without this patch, AI Bot users with no DocPerm
	# rows fail at db_query.py:1005 before any hook fires.
	original_get_role_permissions = fp.get_role_permissions

	def patched_get_role_permissions(doctype_meta, *args, **kwargs):
		# Resolve user from kwargs, positional args, or session.
		# Upstream signature: get_role_permissions(doctype_meta, user=None, is_owner=None, debug=False)
		user = kwargs.get("user")
		if user is None and len(args) >= 1:
			user = args[0]
		if user is None:
			user = getattr(frappe.session, "user", None)

		try:
			if _is_ai_bot(user):
				# Return a fresh copy so callers can mutate without poisoning
				# the module-level constant.
				return dict(_FULL_READ_ROLE_PERMS, if_owner={})
		except Exception:
			pass

		return original_get_role_permissions(doctype_meta, *args, **kwargs)

	fp.get_role_permissions = patched_get_role_permissions

	_PATCH_INSTALLED = True


def before_request():
	"""Hook entry point — install the patch before handling each request."""
	install_permission_patch()


def before_job():
	"""Hook entry point — install the patch before background jobs run."""
	install_permission_patch()


# ----- Supplementary hooks (wired in hooks.py) -----------------------------


def ai_bot_has_permission(doc, ptype="read", user=None):
	"""Standard has_permission hook — kept as belt-and-braces.

	Only fires when the role-based check has already passed, so it can't grant
	access on its own; the monkey-patch above is the real mechanism. This stays
	in place so that document-level controllers calling has_permission with a
	`doc` argument also see the AI Bot bypass.
	"""
	if ptype not in ALLOWED_PTYPES:
		return None
	user = user or frappe.session.user
	if ROLE in frappe.get_roles(user):
		return True
	return None


def ai_bot_query_conditions(user=None, doctype=None):
	"""Strip row-level filters for AI Bot in list/report SQL queries.

	An empty string means 'no extra WHERE clause' — i.e. allow all rows.
	Returning None lets standard query conditions apply for other users.
	"""
	user = user or frappe.session.user
	if ROLE in frappe.get_roles(user):
		return ""
	return None

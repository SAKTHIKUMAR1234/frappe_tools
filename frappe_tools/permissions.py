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
_REPORT_PAGE_PATCH_INSTALLED = False


# Read-style ptypes that AI Bot is allowed to escalate. Anything not in this
# set falls through to the user's actual role permissions — so a user with
# AI Bot + (e.g.) Sales User still gets normal write/create/delete from their
# other role.
_READ_PTYPES = ("read", "select", "report", "print", "email", "export")


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
		# Upstream signature: has_permission(doctype, ptype, doc, user, raise_exception, *, ...)
		# After doctype+ptype are bound, args[0]=doc, args[1]=user.
		user = kwargs.get("user")
		if user is None and len(args) >= 2:
			user = args[1]
		if user is None:
			user = getattr(frappe.session, "user", None)

		if (
			isinstance(doctype, str)
			and ptype in ALLOWED_PTYPES
			and user != "Administrator"  # Administrator already shortcuts to True
			and _is_ai_bot(user)
		):
			return True

		return original_has_permission(doctype, ptype, *args, **kwargs)

	fp.has_permission = patched_has_permission
	# NOTE: do NOT also overwrite `frappe.has_permission`. That symbol is a
	# wrapper in frappe/__init__.py which translates `throw=True` →
	# `raise_exception=True` before calling `frappe.permissions.has_permission`.
	# Replacing it would forward `throw` straight through and blow up with
	# TypeError. The wrapper already routes through our patched
	# `fp.has_permission`, so patching just the inner function is enough.

	# ----- Patch get_role_permissions -----------------------------------------
	# DatabaseQuery.build_match_conditions calls this directly and bypasses
	# has_permission entirely. Without this patch, AI Bot users with no DocPerm
	# rows fail at db_query.py:1005 before any hook fires.
	original_get_role_permissions = fp.get_role_permissions

	def patched_get_role_permissions(doctype_meta, *args, **kwargs):
		# Always call the original first so we get whatever real permissions
		# the user already has from their other roles. Then OR-in the AI Bot
		# read-style flags. This is critical: a user with AI Bot + Sales User
		# must keep their Sales User write/create/delete perms — we only add,
		# never subtract.
		result = original_get_role_permissions(doctype_meta, *args, **kwargs)

		# Resolve user from kwargs, positional args, or session.
		# Upstream signature: get_role_permissions(doctype_meta, user=None, is_owner=None, debug=False)
		user = kwargs.get("user")
		if user is None and len(args) >= 1:
			user = args[0]
		if user is None:
			user = getattr(frappe.session, "user", None)

		try:
			if user != "Administrator" and _is_ai_bot(user):
				# Shallow-copy so we don't mutate the cached dict held by
				# frappe.local.role_permissions.
				if isinstance(result, dict):
					result = dict(result)
					for ptype in _READ_PTYPES:
						result[ptype] = 1
		except Exception:
			pass

		return result

	fp.get_role_permissions = patched_get_role_permissions

	_PATCH_INSTALLED = True

	# Page/Report patches are tracked separately because they may fail at
	# very early import time (before Frappe is fully bootstrapped).
	_install_report_page_patch()


def _install_report_page_patch():
	"""Patch Report.is_permitted and Page.is_permitted for AI Bot bypass.

	These methods check the `Has Role` child table on the Report/Page record
	(and Custom Role entries) directly via `frappe.get_all` + `has_common`,
	completely bypassing `frappe.permissions`. The has_permission and
	get_role_permissions patches don't help here. We patch the controller
	methods so AI Bot users short-circuit to True regardless of which roles
	the report/page is restricted to.

	Tracked via its own flag so we can retry if early-import failed.
	"""
	global _REPORT_PAGE_PATCH_INSTALLED
	if _REPORT_PAGE_PATCH_INSTALLED:
		return

	try:
		from frappe.core.doctype.report.report import Report
		from frappe.core.doctype.page.page import Page
	except Exception:
		# Frappe not fully bootstrapped yet — before_request will retry.
		return

	original_report_is_permitted = Report.is_permitted

	def patched_report_is_permitted(self):
		if _is_ai_bot(getattr(frappe.session, "user", None)):
			return True
		return original_report_is_permitted(self)

	Report.is_permitted = patched_report_is_permitted

	original_page_is_permitted = Page.is_permitted

	def patched_page_is_permitted(self):
		if _is_ai_bot(getattr(frappe.session, "user", None)):
			return True
		return original_page_is_permitted(self)

	Page.is_permitted = patched_page_is_permitted

	_REPORT_PAGE_PATCH_INSTALLED = True


def before_request():
	"""Hook entry point — install the patch before handling each request."""
	install_permission_patch()
	# Retry Page/Report patch in case it failed at very early import time.
	_install_report_page_patch()


def before_job():
	"""Hook entry point — install the patch before background jobs run."""
	install_permission_patch()
	_install_report_page_patch()


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

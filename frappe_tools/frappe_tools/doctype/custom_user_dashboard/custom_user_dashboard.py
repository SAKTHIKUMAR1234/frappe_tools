"""Custom User Dashboard — an HTML dashboard an AI Bot creates and shares.

The AI Bot (read-only everywhere else) may create these and edit ONLY its own
(if_owner). Each dashboard is either PUBLIC (require_auth off — anyone with the
link) or restricted to a listed set of logged-in users (require_auth on). The
stored HTML is rendered inside a sandboxed iframe by the web/desk view, so it
can never use a viewer's login session to reach other data.
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document


class CustomUserDashboard(Document):
	def autoname(self):
		if not self.route:
			self.route = _slugify(self.title)
		self.name = self.route

	def validate(self):
		if not self.route:
			self.route = _slugify(self.title)
		self.route = _slugify(self.route)
		if not self.route:
			frappe.throw(_("A title (or route) is required"))
		# An AI Bot edits only its own dashboard — belt-and-braces beyond if_owner.
		if not self.is_new() and "System Manager" not in frappe.get_roles():
			owner = frappe.db.get_value("Custom User Dashboard", self.name, "owner")
			if owner and owner != frappe.session.user:
				frappe.throw(_("Only the creator of this dashboard can edit it"),
					frappe.PermissionError)

	def can_view(self, user=None):
		"""Access rule: public dashboards are open to anyone; restricted ones are
		open only to the listed users (and the owner / System Manager)."""
		if not self.enabled:
			return False
		if not self.require_auth:
			return True
		user = user or frappe.session.user
		if user == "Guest":
			return False
		if user == self.owner or "System Manager" in frappe.get_roles(user):
			return True
		return any((r.user == user) for r in (self.allowed_users or []))


def _slugify(text):
	return re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")[:140]


@frappe.whitelist()
def get_my_dashboards():
	"""Custom User Dashboards the current user may open — ones they own or are
	an allowed viewer of. Filtered by our own access rule, not doctype DocPerm
	(viewers reach dashboards through the allow-list, not through the doctype)."""
	user = frappe.session.user
	if user == "Guest":
		return []
	return frappe.db.sql("""
		SELECT DISTINCT d.name, d.route, d.title, d.require_auth, d.owner, d.modified
		FROM `tabCustom User Dashboard` d
		LEFT JOIN `tabCustom User Dashboard User` u
		       ON u.parent = d.name AND u.parenttype = 'Custom User Dashboard'
		WHERE d.enabled = 1 AND (d.owner = %s OR u.user = %s)
		ORDER BY d.modified DESC
	""", (user, user), as_dict=True)


# ---------------------------------------------------------------- AI Bot API
# Three whitelisted endpoints the AI Bot uses to manage its dashboards. Each
# writes through the normal ORM (no ignore_permissions), so the AI Bot write
# guard + if_owner + validate() enforce: only the AI Bot / System Manager can
# call them, and a bot may only touch dashboards it created. Documented in the
# shipped skill file (frappe_tools/skills/custom_user_dashboard/SKILL.md).


def _parse_users(allowed_users):
	"""Accept a JSON list, a python list, or a comma/newline string of emails."""
	if not allowed_users:
		return []
	if isinstance(allowed_users, str):
		allowed_users = allowed_users.strip()
		if allowed_users.startswith("["):
			try:
				allowed_users = frappe.parse_json(allowed_users)
			except Exception:
				allowed_users = allowed_users.replace("\n", ",").split(",")
		else:
			allowed_users = allowed_users.replace("\n", ",").split(",")
	out = []
	for u in allowed_users:
		u = (u or "").strip()
		if u and u not in out:
			out.append(u)
	return out


def _as_result(doc):
	return {
		"name": doc.name,
		"route": doc.route,
		"url": "/user-dashboard?name=" + doc.route,
		"title": doc.title,
		"require_auth": frappe.utils.cint(doc.require_auth),
		"enabled": frappe.utils.cint(doc.enabled),
		"allowed_users": [r.user for r in (doc.allowed_users or [])],
	}


@frappe.whitelist(methods=["POST"])
def create_dashboard(title, html=None, require_auth=1, allowed_users=None, route=None):
	"""Create a dashboard. Returns its name, route and public url.
	Args: title (str, required); html (str, the HTML body); require_auth
	(1/0, default 1 = restricted to allowed_users; 0 = public link);
	allowed_users (list/CSV of user emails, used when require_auth=1);
	route (optional slug; auto-derived from title when omitted)."""
	doc = frappe.new_doc("Custom User Dashboard")
	doc.title = title
	doc.html_content = html or ""
	doc.require_auth = frappe.utils.cint(require_auth)
	if route:
		doc.route = route
	for u in _parse_users(allowed_users):
		doc.append("allowed_users", {"user": u})
	doc.insert()  # perms enforced (AI Bot write guard + create perm)
	return _as_result(doc)


@frappe.whitelist()
def list_dashboards():
	"""List the dashboards the caller OWNS (for management). Returns name,
	route, url, title, enabled, require_auth, modified."""
	rows = frappe.get_all("Custom User Dashboard",
		filters={"owner": frappe.session.user},
		fields=["name", "route", "title", "enabled", "require_auth", "modified"],
		order_by="modified desc")
	for r in rows:
		r["url"] = "/user-dashboard?name=" + (r.get("route") or r["name"])
	return rows


@frappe.whitelist(methods=["POST"])
def update_dashboard(name, html=None, title=None, require_auth=None,
		allowed_users=None, disable=None, delete=None):
	"""Update / disable / delete a dashboard the caller OWNS.
	Args: name (str, required — the dashboard's name/route). Then any of:
	html (new HTML), title, require_auth (1/0), allowed_users (replaces the
	list), disable (1 = turn off so no one can view it; 0 = re-enable),
	delete (1 = permanently remove it). Ownership is enforced — a bot can only
	change its own dashboards."""
	if not frappe.db.exists("Custom User Dashboard", name):
		frappe.throw(frappe._("Dashboard {0} not found").format(name))

	if frappe.utils.cint(delete):
		frappe.delete_doc("Custom User Dashboard", name)  # perms enforced
		return {"deleted": name}

	doc = frappe.get_doc("Custom User Dashboard", name)
	if html is not None:
		doc.html_content = html
	if title is not None:
		doc.title = title
	if require_auth is not None:
		doc.require_auth = frappe.utils.cint(require_auth)
	if disable is not None:
		doc.enabled = 0 if frappe.utils.cint(disable) else 1
	if allowed_users is not None:
		doc.set("allowed_users", [])
		for u in _parse_users(allowed_users):
			doc.append("allowed_users", {"user": u})
	doc.save()  # if_owner + validate() enforce owner-only edit
	return _as_result(doc)

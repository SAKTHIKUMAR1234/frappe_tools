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

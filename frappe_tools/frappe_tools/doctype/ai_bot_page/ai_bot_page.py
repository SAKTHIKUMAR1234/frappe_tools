"""AI Bot Page — an HTML page an AI Bot creates and shares.

The AI Bot (read-only everywhere else) may create these and edit ONLY its own
(if_owner). Each page is either PUBLIC (require_auth off — anyone with the link)
or restricted to a listed set of logged-in users (require_auth on). The stored
HTML is rendered inside a sandboxed iframe by the web/desk view, so it can never
use a viewer's login session to reach other data.
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document


class AIBotPage(Document):
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
		# An AI Bot edits only its own page — belt-and-braces beyond if_owner.
		if not self.is_new() and "System Manager" not in frappe.get_roles():
			owner = frappe.db.get_value("AI Bot Page", self.name, "owner")
			if owner and owner != frappe.session.user:
				frappe.throw(_("Only the creator of this page can edit it"),
					frappe.PermissionError)

	def can_view(self, user=None):
		"""Access rule: public pages are open to anyone; restricted pages are
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
def get_my_ai_pages():
	"""AI Bot Pages the current user may open — ones they own or are an allowed
	viewer of. Filtered by our own access rule, not doctype DocPerm (viewers
	reach pages through the page's allow-list, not through the doctype)."""
	user = frappe.session.user
	if user == "Guest":
		return []
	rows = frappe.db.sql("""
		SELECT DISTINCT p.name, p.route, p.title, p.require_auth, p.owner, p.modified
		FROM `tabAI Bot Page` p
		LEFT JOIN `tabAI Bot Page User` u
		       ON u.parent = p.name AND u.parenttype = 'AI Bot Page'
		WHERE p.enabled = 1 AND (p.owner = %s OR u.user = %s)
		ORDER BY p.modified DESC
	""", (user, user), as_dict=True)
	return rows

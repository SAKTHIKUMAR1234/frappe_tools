"""Public render route for an AI Bot Page: /ai-page?name=<route>.

Access rule (enforced here, server-side):
  - require_auth OFF → public: anyone with the link, incl. logged-out Guests.
  - require_auth ON  → only the page's allowed users (or owner / System Manager),
    and never a Guest.

The stored HTML is handed to the template and rendered inside a sandboxed
iframe (see ai-page.html) so it can never use the viewer's session/cookies.
"""

import frappe
from frappe import _

no_cache = 1


def get_context(context):
	route = frappe.form_dict.get("name") or frappe.form_dict.get("route")
	if not route:
		frappe.throw(_("No page specified"), frappe.PageDoesNotExistError)

	name = frappe.db.get_value("AI Bot Page", {"route": route}) or (
		route if frappe.db.exists("AI Bot Page", route) else None)
	if not name:
		frappe.throw(_("Page not found"), frappe.PageDoesNotExistError)

	# read with ignore_permissions — the doctype is AI-Bot/owner scoped, but a
	# viewer (possibly Guest, possibly a different user) must reach it through
	# the page's OWN can_view rule, not the doctype's DocPerm.
	page = frappe.get_doc("AI Bot Page", name)
	if not page.can_view():
		if frappe.session.user == "Guest":
			raise frappe.Redirect("/login?redirect-to=" + frappe.utils.quoted(frappe.request.full_path))
		frappe.throw(_("You are not allowed to view this page"), frappe.PermissionError)

	context.no_cache = 1
	context.page_title = page.title
	context.ai_html = page.html_content or ""
	context.show_sidebar = False
	return context

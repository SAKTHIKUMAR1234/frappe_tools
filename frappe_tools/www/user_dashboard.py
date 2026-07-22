"""Public render route for a Custom User Dashboard: /user-dashboard?name=<route>.

Access rule (enforced here, server-side):
  - require_auth OFF → public: anyone with the link, incl. logged-out Guests.
  - require_auth ON  → only the dashboard's allowed users (or owner / System
    Manager), and never a Guest.

The stored HTML is handed to the template and rendered inside a sandboxed
iframe (see user-dashboard.html) so it can never use the viewer's session.
"""

import frappe
from frappe import _

no_cache = 1


def get_context(context):
	route = frappe.form_dict.get("name") or frappe.form_dict.get("route")
	if not route:
		frappe.throw(_("No dashboard specified"), frappe.PageDoesNotExistError)

	name = frappe.db.get_value("Custom User Dashboard", {"route": route}) or (
		route if frappe.db.exists("Custom User Dashboard", route) else None)
	if not name:
		frappe.throw(_("Dashboard not found"), frappe.PageDoesNotExistError)

	# read with ignore_permissions — the doctype is AI-Bot/owner scoped, but a
	# viewer (possibly Guest, possibly a different user) must reach it through
	# the dashboard's OWN can_view rule, not the doctype's DocPerm.
	dashboard = frappe.get_doc("Custom User Dashboard", name)
	if not dashboard.can_view():
		if frappe.session.user == "Guest":
			raise frappe.Redirect("/login?redirect-to=" + frappe.utils.quoted(frappe.request.full_path))
		frappe.throw(_("You are not allowed to view this dashboard"), frappe.PermissionError)

	context.no_cache = 1
	context.page_title = dashboard.title
	context.ai_html = dashboard.html_content or ""
	context.show_sidebar = False
	# Render edge-to-edge: drop the Bootstrap .container (max-width ~1140px) that
	# templates/web.html wraps page_content in, so the dashboard fills the full
	# viewport width (same flag the Web Page doctype exposes as "Full Width").
	context.full_width = 1
	return context

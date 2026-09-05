import json
from pathlib import Path

import frappe

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/ocr"
		raise frappe.Redirect
	manifest = Path(frappe.get_app_path("frappe_tools", "public", "ocr_ui", ".vite", "manifest.json"))
	if not manifest.is_file():
		frappe.throw("Build the document review interface with bench build --app frappe_tools.")
	entry = json.loads(manifest.read_text())["index.html"]
	base = "/assets/frappe_tools/ocr_ui/"
	context.entry_script = base + entry["file"]
	context.entry_styles = [base + name for name in entry.get("css", [])]
	context.no_cache = 1
	context.csrf_token = frappe.sessions.get_csrf_token()
	return context

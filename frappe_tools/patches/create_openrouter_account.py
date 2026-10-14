import frappe
from frappe.utils.password import get_decrypted_password


def execute() -> None:
	if frappe.db.exists("AI Provider Account", "OpenRouter"):
		return
	settings = "Document Extraction Settings"
	key = get_decrypted_password(settings, settings, "openrouter_api_key", raise_exception=False)
	if key:
		frappe.get_doc(
			{
				"doctype": "AI Provider Account",
				"account_name": "OpenRouter",
				"provider": "OpenRouter",
				"auth_type": "API Key",
				"enabled": 1,
				"api_key": key,
			}
		).insert(ignore_permissions=True)

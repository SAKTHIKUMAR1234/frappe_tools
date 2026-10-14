import frappe


def execute() -> None:
	if frappe.db.exists("AI Provider Account", "OpenRouter"):
		return
	settings = frappe.get_single("Document Extraction Settings")
	key = settings.get_password("openrouter_api_key", raise_exception=False)
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

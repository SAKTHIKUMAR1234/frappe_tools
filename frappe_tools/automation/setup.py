"""Idempotent backend-owned automation model definitions."""

import frappe


def ensure_models():
	name = "Document Automation Luna"
	if frappe.db.exists("AI Model", name):
		doc = frappe.get_doc("AI Model", name)
		# A migration may seed a missing model, but must never replace the site's
		# provider, model, credentials, capabilities or enabled/disabled choice.
		return {"model": name, "created": False, "provider": doc.provider,
			"credential_configured": doc.provider == "Codex OAuth" or bool(doc.get_password("api_key", raise_exception=False))}
	doc = frappe.get_doc({"doctype": "AI Model", "model_label": name, "enabled": 1, "provider": "Codex OAuth",
		"model_id": "gpt-5.6-luna", "auth_type": "Managed OAuth", "supports_vision": 1, "supports_json_mode": 1,
		"max_tokens": 8192, "temperature": 0, "agent_max_concurrency": 2,
		"agent_timeout_seconds": 300, "agent_queue_timeout_seconds": 30,
		"notes": "Codex OAuth document vision and decision agent. OAuth is managed by Codex App Server; document extraction has no fallback provider."})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"model": name, "created": True, "provider": doc.provider, "credential_configured": True}


def setup_document_automation():
	"""Seed missing defaults while preserving site-owned model/profile settings."""
	from frappe_tools.extractors.v1_setup import ensure_default_layouts, install_profiles
	from frappe_tools.extractors.ui import setup_adapters

	model = ensure_models()
	verifier = ensure_verifier_model()
	adapters = setup_adapters()
	profiles = install_profiles()
	layouts = ensure_default_layouts()
	return {"model": model, "verifier": verifier, "profiles": profiles, "layouts": layouts, "adapters": adapters}


def ensure_verifier_model():
	"""Seed the separate Sol verifier without rewriting operator-owned settings."""
	name = "Document Verification Sol"
	if frappe.db.exists("AI Model", name):
		return {"model": name, "created": False}
	frappe.get_doc({
		"doctype": "AI Model", "model_label": name, "enabled": 1,
		"provider": "Codex OAuth", "model_id": "gpt-5.6-sol", "auth_type": "Managed OAuth",
		"supports_vision": 1, "supports_json_mode": 1, "max_tokens": 8192,
		"temperature": 0, "agent_max_concurrency": 1, "agent_timeout_seconds": 300,
		"agent_queue_timeout_seconds": 30,
		"notes": "Verifies Luna extraction against adapter references and read-only tools. Stages reviewed values; never writes business documents directly. No fallback provider.",
	}).insert(ignore_permissions=True)
	return {"model": name, "created": True}

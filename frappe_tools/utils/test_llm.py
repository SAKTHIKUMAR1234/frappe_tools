from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from frappe_tools.utils import llm


class TestDocumentExtractionProviderReadiness(FrappeTestCase):
	@patch("frappe_tools.automation.agent_providers.call_vision")
	@patch("frappe_tools.utils.llm.ensure_ready")
	def test_vision_makes_exactly_one_codex_call(self, ensure_ready, call_vision):
		model = SimpleNamespace(name="Document Automation Luna", provider="Codex OAuth")
		ensure_ready.return_value = (SimpleNamespace(), model)
		call_vision.return_value = {"data": {"fields": []}}
		result = llm.call_vision(["data:image/png;base64,AA=="], "system", "user", extraction="DOC-1")
		self.assertEqual(result["data"], {"fields": []})
		call_vision.assert_called_once()
	@patch("frappe_tools.automation.agent_providers.health")
	@patch("frappe_tools.utils.llm.frappe.get_doc")
	@patch("frappe_tools.utils.llm.frappe.db.exists", return_value=True)
	@patch("frappe_tools.utils.llm.get_settings")
	def test_ready_provider_is_codex_without_credentials(self, get_settings, _exists, get_doc, health):
		settings = SimpleNamespace(enable=True, vision_ai_model="Document Automation Luna")
		get_settings.return_value = settings
		get_doc.return_value = SimpleNamespace(
			name="Document Automation Luna", enabled=1, provider="Codex OAuth", model_id="gpt-5.6-luna",
			supports_vision=1, fallback_model=None,
		)
		health.return_value = {"ready": True, "reason": None}
		status = llm.readiness()
		self.assertEqual(status, {
			"ready": True, "provider": "Codex OAuth", "model": "gpt-5.6-luna", "reason": None,
		})

	@patch("frappe_tools.utils.llm.frappe.get_doc")
	@patch("frappe_tools.utils.llm.frappe.db.exists", return_value=True)
	@patch("frappe_tools.utils.llm.get_settings")
	def test_openrouter_without_key_is_not_ready(self, get_settings, _exists, get_doc):
		get_settings.return_value = SimpleNamespace(enable=True, vision_ai_model="Vision Model")
		get_doc.return_value = SimpleNamespace(
			name="Vision Model", enabled=1, provider="OpenRouter", supports_vision=1, fallback_model=None,
			get_password=Mock(return_value=None),
		)
		status = llm.readiness()
		self.assertFalse(status["ready"])
		self.assertIn("Configure the API key", status["reason"])

	@patch("frappe_tools.automation.agent_providers.health", side_effect=AssertionError("Do not inspect a different provider"))
	@patch("frappe_tools.utils.llm.frappe.get_doc")
	@patch("frappe_tools.utils.llm.frappe.db.exists", return_value=True)
	@patch("frappe_tools.utils.llm.get_settings")
	def test_openrouter_readiness_is_independent_of_codex(self, get_settings, _exists, get_doc, _health):
		get_settings.return_value = SimpleNamespace(enable=True, vision_ai_model="Vision Model")
		get_doc.return_value = SimpleNamespace(
			name="Vision Model", model_id="configured-vision", enabled=1, provider="OpenRouter",
			supports_vision=1, fallback_model=None, get_password=Mock(return_value="test-only"),
		)
		self.assertEqual(llm.readiness(), {"ready": True, "provider": "OpenRouter", "model": "configured-vision", "reason": None})

	@patch("frappe_tools.i2a.providers.call_model")
	@patch("frappe_tools.utils.llm.ensure_ready")
	def test_openrouter_vision_uses_its_configured_transport_and_audit_link(self, ensure_ready, call_model):
		model = SimpleNamespace(name="Vision Model", model_id="configured-vision", provider="OpenRouter")
		ensure_ready.return_value = (SimpleNamespace(), model)
		call_model.return_value = {"data": {"fields": []}}
		result = llm.call_vision(["data:image/png;base64,AA=="], "system", "user", extraction="DOC-1", target_doctype="Purchase Invoice")
		self.assertEqual(result["model"], "configured-vision")
		self.assertEqual(call_model.call_args.kwargs, {"purpose": "document_vision", "run": "DOC-1", "action": "Purchase Invoice"})
		self.assertEqual(call_model.call_args.args[1][1]["content"][1]["type"], "image_url")

	@patch("frappe_tools.i2a.providers.call_model")
	@patch("frappe_tools.utils.llm.ensure_ready")
	def test_remote_image_urls_are_not_forwarded(self, ensure_ready, call_model):
		ensure_ready.return_value = (None, SimpleNamespace(provider="OpenRouter"))
		with self.assertRaises(frappe.ValidationError):
			llm.call_vision(["https://example.invalid/private.png"], "system", "user")
		call_model.assert_not_called()

	@patch("frappe_tools.utils.llm.frappe.get_doc")
	@patch("frappe_tools.utils.llm.frappe.db.exists", return_value=True)
	@patch("frappe_tools.utils.llm.get_settings")
	def test_fallback_model_is_rejected(self, get_settings, _exists, get_doc):
		get_settings.return_value = SimpleNamespace(enable=True, vision_ai_model="Document Automation Luna")
		get_doc.return_value = SimpleNamespace(
			name="Document Automation Luna", enabled=1, provider="Codex OAuth", supports_vision=1,
			fallback_model="Anything Else",
		)
		status = llm.readiness()
		self.assertFalse(status["ready"])
		self.assertIn("fail closed", status["reason"])

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from frappe_tools.i2a import intents


class TestI2AIntents(TestCase):
	@patch("frappe_tools.i2a.intents.providers.call_model")
	@patch("frappe_tools.i2a.intents.frappe")
	def test_intent_uses_action_orchestrator_and_keeps_media_ephemeral(
		self, frappe_mock, call_model
	):
		action = MagicMock()
		action.name = "WhatsApp Summary"
		action.enabled = 1
		action.instructions = "Summarize"
		action.knowledge = "Business context"
		action.rules = "Do not guess"
		action.parsed_schema.return_value = [{"key": "summary"}]
		action.models = [SimpleNamespace(ai_model="Fast Model", is_orchestrator=1)]
		frappe_mock.db.exists.return_value = True
		frappe_mock.get_doc.return_value = action
		call_model.return_value = {
			"data": {"summary": "Customer requested a callback"},
			"usage": {"total_tokens": 12},
			"latency_ms": 45,
		}

		result = intents.run_intent(
			"WhatsApp Summary",
			"One new voice note",
			content_parts=[{
				"type": "input_audio",
				"input_audio": {"data": "encoded", "format": "ogg"},
			}],
		)

		self.assertEqual(result["data"]["summary"], "Customer requested a callback")
		self.assertEqual(result["model"], "Fast Model")
		messages = call_model.call_args.args[1]
		self.assertEqual(messages[1]["content"][1]["type"], "input_audio")
		self.assertEqual(call_model.call_args.kwargs["action"], "WhatsApp Summary")

	def test_provider_audit_redacts_all_embedded_media(self):
		body = {
			"messages": [{
				"content": [
					{"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
					{"type": "input_audio", "input_audio": {"data": "voice-secret", "format": "ogg"}},
					{"type": "file", "file": {"filename": "proof.pdf", "file_data": "pdf-secret"}},
				]
			}],
		}

		redacted = intents.providers._redact_images(body)

		serialized = str(redacted)
		self.assertNotIn("voice-secret", serialized)
		self.assertNotIn("pdf-secret", serialized)
		self.assertNotIn("base64,abc", serialized)

import json
from unittest.mock import Mock

from frappe.tests.utils import FrappeTestCase

from frappe_tools.ai.providers import ProviderError, anthropic, codex, openrouter

SCHEMA = {
	"type": "object",
	"properties": {"quantity": {"type": "number"}},
	"required": ["quantity"],
	"additionalProperties": False,
}
IMAGE = "data:image/png;base64,bG9jYWw="


def response(result: dict | None = None, status: int = 200, events: list | None = None) -> Mock:
	mock = Mock(status_code=status)
	mock.json.return_value = result or {}
	mock.iter_content.return_value = [json.dumps(result or {}).encode()]
	mock.iter_lines.return_value = [b"data: " + json.dumps(event).encode() for event in events or []]
	return mock


class TestProviders(FrappeTestCase):
	def setUp(self):
		self.account = dict(
			api_key="local-only-key",
			access_token="local-only-access",
			chatgpt_account_id="local-account",
			auth_type="API Key",
		)
		self.args = (
			self.account,
			"user-selected-model",
			"Extract",
			"Quantity",
			[IMAGE],
			SCHEMA,
			dict(temperature=0.2, max_tokens=16000),
		)

	def test_openrouter_schema_and_usage(self):
		url, headers, body = openrouter.build_request(*self.args)
		self.assertTrue(url.endswith("/chat/completions"))
		self.assertEqual(body["response_format"]["json_schema"]["schema"], SCHEMA)
		self.assertEqual(body["max_tokens"], 16000)
		self.assertEqual(body["temperature"], 0.2)
		self.assertEqual(body["usage"], {"include": True})
		self.assertTrue(headers["Authorization"].startswith("Bearer "))

	def test_codex_schema_and_supported_parameters(self):
		url, headers, body = codex.build_request(*self.args)
		self.assertTrue(url.endswith("/codex/responses"))
		self.assertEqual(body["text"]["format"]["schema"], SCHEMA)
		self.assertEqual(body["input"][0]["content"][1]["image_url"], IMAGE)
		self.assertNotIn("max_output_tokens", body)
		self.assertNotIn("temperature", body)
		self.assertEqual(headers["chatgpt-account-id"], "local-account")

	def test_codex_no_schema_uses_prompt(self):
		args = list(self.args)
		args[5] = None
		body = codex.build_request(*args)[2]
		self.assertNotIn("text", body)
		self.assertIn("JSON", body["instructions"])

	def test_codex_delta_only(self):
		text, usage, finish, _raw = codex.parse_response(
			response(
				events=[
					{"type": "response.output_text.delta", "delta": '{"quantity":'},
					{"type": "response.output_text.delta", "delta": "12}"},
					{
						"type": "response.completed",
						"response": {
							"output": [],
							"usage": {"input_tokens": 11, "output_tokens": 5, "total_tokens": 16},
						},
					},
				]
			)
		)
		self.assertEqual(json.loads(text), {"quantity": 12})
		self.assertEqual(usage, dict(prompt_tokens=11, completion_tokens=5, total_tokens=16, cost=0))
		self.assertEqual(finish, "completed")

	def test_codex_completed_only(self):
		text = codex.parse_response(
			response(
				events=[
					{
						"type": "response.completed",
						"response": {
							"output": [
								{
									"type": "message",
									"content": [{"type": "output_text", "text": '{"quantity":12}'}],
								}
							]
						},
					}
				]
			)
		)[0]
		self.assertEqual(json.loads(text), {"quantity": 12})

	def test_codex_error_events_and_incomplete_stream(self):
		for kind in ("error", "response.failed", "response.incomplete"):
			with self.subTest(kind=kind), self.assertRaises(ProviderError):
				codex.parse_response(response(events=[{"type": kind}]))
		with self.assertRaises(ProviderError):
			codex.parse_response(response(events=[{"type": "response.output_text.delta", "delta": "{}"}]))

	def test_anthropic_schema_both_auth_types(self):
		for auth in ("API Key", "Setup Token"):
			self.account["auth_type"] = auth
			_url, headers, body = anthropic.build_request(*self.args)
			self.assertEqual(body["output_config"]["format"]["schema"], SCHEMA)
			self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
			self.assertEqual(body["messages"][0]["content"][1]["source"]["media_type"], "image/png")
			self.assertEqual(body["max_tokens"], 16000)
			if auth == "Setup Token":
				self.assertIn("oauth-2025-04-20", headers["anthropic-beta"])
				self.assertIn("Claude Code", body["system"][1]["text"])
				self.assertNotIn("x-api-key", headers)
				self.assertIn("cc_entrypoint=sdk-cli", body["system"][0]["text"])
				self.assertEqual(headers["x-app"], "cli")
			else:
				self.assertIn("x-api-key", headers)
				self.assertNotIn("Authorization", headers)

	def test_anthropic_json_response(self):
		text, usage, finish, _raw = anthropic.parse_response(
			response(
				{
					"content": [{"type": "text", "text": '{"quantity":12}'}],
					"usage": {"input_tokens": 11, "output_tokens": 5},
					"stop_reason": "end_turn",
				}
			)
		)
		self.assertEqual(json.loads(text), {"quantity": 12})
		self.assertEqual(usage["total_tokens"], 16)
		self.assertEqual(finish, "end_turn")

	def test_anthropic_bad_image(self):
		args = list(self.args)
		args[4] = ["https://example.test/image.png"]
		with self.assertRaises(ValueError):
			anthropic.build_request(*args)

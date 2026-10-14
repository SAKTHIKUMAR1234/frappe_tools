import json
from unittest.mock import Mock

import frappe
from frappe.tests import UnitTestCase

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


def ai_task_rows(model_id: str, **model_fields) -> list[dict]:
	"""Insert a local OpenRouter account and AI Model; return one enabled AI Task Models row per task."""
	from frappe_tools.ai.client import TASKS

	account = frappe.get_doc(dict(doctype="AI Provider Account", account_name="LOCAL-" + frappe.generate_hash(length=10),
		provider="OpenRouter", auth_type="API Key", api_key="local-test-key", enabled=1)).insert()
	model = frappe.get_doc(dict(doctype="AI Model", model_label=f"{account.name} {model_id}", account=account.name,
		model_id=model_id, supports_vision=1, **model_fields)).insert()
	return [frappe._dict(task=task, ai_model=model.name, enabled=1) for task in TASKS]


class TestProviders(UnitTestCase):
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

	def test_json_mode_off_drops_json_format_and_instruction(self):
		options = dict(temperature=0, max_tokens=100, json_mode=False)
		for adapter in (openrouter, codex, anthropic):
			with self.subTest(adapter=adapter.__name__):
				body = adapter.build_turn(self.account, "model", "System", [], None, options)[2]
				self.assertNotIn("response_format", body)
				self.assertNotIn("JSON", json.dumps(body))

	def test_chat_messages_convert_for_each_provider(self):
		messages = [
			{"role": "system", "content": "Rules"},
			{"role": "user", "content": [{"type": "text", "text": "Read"}, {"type": "image_url", "image_url": {"url": IMAGE}}]},
			{"role": "assistant", "content": "", "tool_calls": [
				{"id": "call_1", "type": "function", "function": {"name": "find", "arguments": '{"q": "x"}'}}]},
			{"role": "tool", "tool_call_id": "call_1", "content": "found"},
		]
		system, turns = openrouter.chat_messages(messages)
		self.assertEqual((system, turns), ("Rules", messages[1:]))

		system, items = codex.chat_messages(messages)
		self.assertEqual(system, "Rules")
		self.assertEqual(items[0]["content"][1], {"type": "input_image", "image_url": IMAGE})
		self.assertEqual(items[1], {"type": "function_call", "call_id": "call_1", "name": "find", "arguments": '{"q": "x"}'})
		self.assertEqual(items[2], {"type": "function_call_output", "call_id": "call_1", "output": "found"})

		system, turns = anthropic.chat_messages(messages)
		self.assertEqual([turn["role"] for turn in turns], ["user", "assistant", "user"])
		self.assertEqual(turns[0]["content"][1]["source"]["data"], "bG9jYWw=")
		self.assertEqual(turns[1]["content"], [{"type": "tool_use", "id": "call_1", "name": "find", "input": {"q": "x"}}])
		self.assertEqual(turns[2]["content"][0]["tool_use_id"], "call_1")

	def test_chat_messages_reject_unsupported_parts(self):
		audio = [{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": "x", "format": "wav"}}]}]
		for adapter in (codex, anthropic):
			with self.subTest(adapter=adapter.__name__), self.assertRaises(ValueError):
				adapter.chat_messages(audio)

	def test_assistant_message_is_openai_chat_shape(self):
		reply = {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "function": {"name": "f", "arguments": "{}"}}]}
		self.assertEqual(openrouter.assistant_message({"choices": [{"message": reply}]}, ""), reply)
		result = {"output": [{"type": "function_call", "call_id": "c", "name": "f", "arguments": "{}"}]}
		message = codex.assistant_message(result, "")
		self.assertEqual(message["tool_calls"][0]["function"], {"name": "f", "arguments": "{}"})
		message = anthropic.assistant_message({"content": [{"type": "tool_use", "id": "c", "name": "f", "input": {"a": 1}}]}, "")
		self.assertEqual(message["tool_calls"][0]["function"]["arguments"], '{"a": 1}')

import hashlib
import hmac
import json
import time
from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase

from frappe_tools.ai import client
from frappe_tools.ai.providers import ProviderError, anthropic, codex, genie, openrouter

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


class TestGenieProvider(UnitTestCase):
	"""The Genie hooks device as a provider: a signed call in, the playbook's reply mapped back."""

	SECRET = "local-genie-secret-local-genie-secret"

	def setUp(self):
		self.account = dict(provider="Genie", auth_type="Shared Secret", api_key=self.SECRET,
			base_url="http://127.0.0.1:8796/", request_timeout=30)
		self.options = dict(temperature=0, max_tokens=1200, timeout=60)

	def call(self, body: dict) -> dict:
		return json.loads(body["request"])

	def test_request_is_signed_over_the_call_text(self):
		url, headers, body = genie.build_request(self.account, "genie", "Rules", "Hello", [], SCHEMA, self.options)
		self.assertEqual(url, "http://127.0.0.1:8796/provider")
		expected = "sha256=" + hmac.new(self.SECRET.encode(), body["request"].encode(), hashlib.sha256).hexdigest()
		self.assertEqual(headers["X-Hub-Signature-256"], expected)
		call = self.call(body)
		self.assertEqual(headers["Idempotency-Key"], call["id"])
		self.assertEqual(call["model"], "genie")
		self.assertEqual(call["system"], "Rules")
		self.assertEqual(call["messages"], [{"role": "user", "content": "Hello"}])
		self.assertEqual(call["response_format"], {"type": "json_schema", "schema": SCHEMA})
		self.assertEqual(call["max_tokens"], 1200)
		self.assertLessEqual(abs(call["sent_at"] - time.time()), 5)
		self.assertNotIn(self.SECRET, json.dumps([url, headers, body]))
		second = genie.build_request(self.account, "genie", "Rules", "Hello", [], SCHEMA, self.options)[2]
		self.assertNotEqual(self.call(second)["id"], call["id"])

	def test_wait_is_the_shorter_timeout_less_the_margin(self):
		for account_timeout, request_timeout, wait in ((30, 60, 27), (None, 60, 47), (90, 20, 17), (2, 60, 1)):
			with self.subTest(account_timeout=account_timeout, request_timeout=request_timeout):
				account = {**self.account, "request_timeout": account_timeout}
				body = genie.build_turn(account, "genie", "S", [], None, {**self.options, "timeout": request_timeout})[2]
				self.assertEqual(self.call(body)["timeout_s"], wait)

	def test_response_format_follows_json_mode(self):
		formats = {True: {"type": "json_object"}, False: {"type": "text"}}
		for json_mode, expected in formats.items():
			body = genie.build_turn(self.account, "genie", "S", [], None, {**self.options, "json_mode": json_mode})[2]
			self.assertEqual(self.call(body)["response_format"], expected)

	def test_tools_and_the_final_turn(self):
		tools = [{"name": "SEARCH", "description": "Find", "parameters": {"type": "object"}}]
		body = genie.build_turn(self.account, "genie", "S", [], SCHEMA, self.options, tools)[2]
		self.assertEqual(body["tools"], ["SEARCH"])
		self.assertEqual((self.call(body)["tools"], self.call(body)["tool_choice"]), (tools, "auto"))
		final = genie.build_turn(self.account, "genie", "S", [], SCHEMA, self.options, tools, final=True)[2]
		self.assertEqual(self.call(final)["tool_choice"], "none")
		plain = genie.build_turn(self.account, "genie", "S", [], SCHEMA, self.options)[2]
		self.assertNotIn("tools", plain)
		self.assertNotIn("tools", self.call(plain))

	def test_refuses_images_and_a_missing_url(self):
		with self.assertRaises(ValueError):
			genie.build_request(self.account, "genie", "S", "U", [IMAGE], None, self.options)
		with self.assertRaises(ProviderError):
			genie.build_request({**self.account, "base_url": ""}, "genie", "S", "U", [], None, self.options)
		with self.assertRaises(ValueError):
			genie.chat_messages([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": IMAGE}}]}])

	def test_answer_and_error_mapping(self):
		text, usage, finish, raw = genie.parse_response(
			response({"id": "c", "model": "genie", "content": '{"quantity":12}', "finish_reason": "stop"})
		)
		self.assertEqual(json.loads(text), {"quantity": 12})
		self.assertEqual(usage, dict(prompt_tokens=0, completion_tokens=0, total_tokens=0, cost=0))
		self.assertEqual((finish, genie.tool_calls(raw)), ("stop", []))
		with self.assertRaises(ProviderError):
			genie.parse_response(response({"error": {"code": "genie_failed", "message": "step_failed"}}))

	def test_tool_calls_continue_in_chat_shape(self):
		result = {"content": "", "finish_reason": "tool_calls",
			"tool_calls": [{"id": "call_1", "name": "SEARCH", "arguments": {"q": "shirt"}}]}
		self.assertEqual(genie.tool_calls(result), [{"id": "call_1", "name": "SEARCH", "arguments": {"q": "shirt"}}])
		reply, output = genie.continuation(result, [(genie.tool_calls(result)[0], '{"rows": []}')])
		self.assertEqual(reply["tool_calls"][0]["function"], {"name": "SEARCH", "arguments": '{"q": "shirt"}'})
		self.assertEqual(output, {"role": "tool", "tool_call_id": "call_1", "content": '{"rows": []}'})
		self.assertEqual(genie.assistant_message(result, "")["tool_calls"], reply["tool_calls"])

	def test_chat_messages_keep_only_what_genie_reads(self):
		messages = [
			{"role": "system", "content": "Rules"},
			{"role": "user", "content": [{"type": "text", "text": "Find"}], "name": "customer"},
			{"role": "assistant", "content": None, "refusal": None, "tool_calls": [
				{"id": "c", "type": "function", "function": {"name": "find", "arguments": "{}"}}]},
			{"role": "tool", "tool_call_id": "c", "content": "found"},
		]
		system, turns = genie.chat_messages(messages)
		self.assertEqual(system, "Rules")
		self.assertEqual(turns, [
			{"role": "user", "content": "Find"},
			{"role": "assistant", "content": "", "tool_calls": [
				{"id": "c", "type": "function", "function": {"name": "find", "arguments": "{}"}}]},
			{"role": "tool", "content": "found", "tool_call_id": "c"},
		])


	def test_account_needs_a_url_and_a_long_secret(self):
		def account(**fields):
			values = dict(doctype="AI Provider Account", account_name="LOCAL-genie", provider="Genie",
				auth_type="Shared Secret", api_key=self.SECRET, base_url="https://genie.example.test")
			return frappe.get_doc({**values, **fields})

		account().validate_genie()
		for fields in (dict(base_url=""), dict(base_url="genie.example.test"), dict(api_key="short"), dict(request_timeout=-1)):
			with self.subTest(fields=fields), self.assertRaises(frappe.ValidationError):
				account(**fields).validate_genie()


class TestGenieThroughTheClient(UnitTestCase):
	"""client.execute and record_failure with a Genie account: success, usage and the error statuses."""

	def setUp(self):
		self.account = dict(provider="Genie", auth_type="Shared Secret", api_key="local-genie-secret-local-genie-secret",
			base_url="http://127.0.0.1:8796", request_timeout=30)
		self.options = dict(temperature=0, max_tokens=1200, timeout=60, attempts=1)

	def build(self, adapter, credentials, model):
		return adapter.build_request(credentials, model, "Rules", "Hello", [], SCHEMA, self.options)

	def run_execute(self, answer: Mock):
		state = client.new_state()
		with patch("requests.post", return_value=answer) as post:
			try:
				client.execute(self.account, "genie", self.build, self.options, state)
			except Exception as exc:
				client.record_failure(state, exc)
				return state, post, exc
		return state, post, None

	def test_success_returns_the_json_answer(self):
		state, post, error = self.run_execute(response({"id": "c", "model": "genie", "content": '{"quantity": 12}', "finish_reason": "stop"}))
		self.assertIsNone(error)
		self.assertEqual(state["data"], {"quantity": 12})
		self.assertEqual(state["usage"]["total_tokens"], 0)
		self.assertEqual(post.call_args.kwargs["timeout"], 60)
		self.assertEqual(post.call_args.args[0], "http://127.0.0.1:8796/provider")

	def test_errors_map_like_the_other_providers(self):
		cases = {
			401: ("auth", "missing or bad X-Hub-Signature-256", False),
			429: ("busy", "Genie is answering 4 calls; try again", True),
			502: ("genie_failed", "Genie could not answer: step_failed", True),
			504: ("timeout", "Genie timed out: no answer in 27 s", True),
		}
		for status, (code, message, retryable) in cases.items():
			with self.subTest(status=status):
				state, _post, error = self.run_execute(response({"error": {"code": code, "message": message}}, status))
				self.assertIsInstance(error, ProviderError)
				self.assertEqual(state["http_status"], status)
				self.assertEqual(state["error"], message)
				self.assertEqual(state["retryable"], retryable)
		self.assertRegex(cases[504][1], r"timed?\s*out")

	def test_a_tool_call_answer_is_not_a_json_error(self):
		tools = [{"name": "SEARCH", "description": "Find", "parameters": {"type": "object"}}]

		def build(adapter, credentials, model):
			return adapter.build_turn(credentials, model, "Rules", [], SCHEMA, self.options, tools)

		state = client.new_state()
		answer = response({"content": "", "finish_reason": "tool_calls", "tool_calls": [{"id": "call_1", "name": "SEARCH", "arguments": {}}]})
		with patch("requests.post", return_value=answer):
			client.execute(self.account, "genie", build, self.options, state)
		self.assertEqual(state["calls"], [{"id": "call_1", "name": "SEARCH", "arguments": {}}])

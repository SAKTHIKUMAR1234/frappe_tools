import json
import time
from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase

from frappe_tools.ai import agent, client
from frappe_tools.ai.agent import Tool
from frappe_tools.ai.test_providers import response
from frappe_tools.utils import llm

SECRET = "local-only-secret-key"
SCHEMA = {
	"type": "object",
	"properties": {"answer": {"type": "string"}},
	"required": ["answer"],
	"additionalProperties": False,
}
FINAL = '{"answer":"ok"}'
CONTEXT_KEYS = ("extraction", "target_doctype", "phase", "reference_doctype", "reference_name", "run_id")


def openrouter_turn(calls=None, content=None):
	tool_calls = [
		{"id": f"call_{index}", "type": "function", "function": {"name": "echo", "arguments": json.dumps(arguments)}}
		for index, arguments in enumerate(calls or [])
	]
	message = {"role": "assistant", "content": content, **({"tool_calls": tool_calls} if tool_calls else {})}
	return response({"choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}]})


def codex_turn(calls=None, content=None):
	events = [
		{"type": "response.output_item.done", "item": {"type": "function_call", "id": f"fc_{index}",
			"call_id": f"call_{index}", "name": "echo", "arguments": json.dumps(arguments)}}
		for index, arguments in enumerate(calls or [])
	]
	if content:
		events.append({"type": "response.output_text.delta", "delta": content})
	events.append({"type": "response.completed", "response": {"output": [], "usage": {"input_tokens": 3, "output_tokens": 2}}})
	return response(events=events)


def anthropic_turn(calls=None, content=None):
	parts = [{"type": "tool_use", "id": f"toolu_{index}", "name": "echo", "input": arguments}
		for index, arguments in enumerate(calls or [])]
	if content:
		parts.append({"type": "text", "text": content})
	return response({"content": parts, "stop_reason": "tool_use" if calls else "end_turn", "usage": {}})


class TestAgent(UnitTestCase):
	def setUp(self):
		self._outcome.result.tb_locals = False
		frappe.set_user("Administrator")
		self.prefix = "LOCAL-AGENT-" + frappe.generate_hash(length=10)
		self.account = self.new_account("OpenRouter", "API Key")
		self.settings = frappe.get_single("Document Extraction Settings")
		self.settings.update(dict(enable=1, max_attempts=1, request_timeout=30, task_models=[]))
		self.settings.append("task_models", dict(task="Verifier", ai_model=self.new_model(self.account, "row-model"), enabled=1))
		self.enterContext(patch.object(llm, "ensure_ready", return_value=self.settings))
		self.enterContext(patch.object(client.time, "sleep"))
		self.post = self.enterContext(patch("requests.post"))
		self.echo = Mock(side_effect=lambda text="": {"echo": text, "secret": SECRET, "note": f"has {SECRET}"})
		self.tools = [Tool("echo", "Echo text", {"type": "object", "properties": {"text": {"type": "string"}}}, self.echo)]

	def tearDown(self):
		frappe.db.rollback()

	def new_account(self, provider, auth_type, suffix=""):
		return frappe.get_doc(dict(doctype="AI Provider Account", account_name=self.prefix + suffix, provider=provider,
			auth_type=auth_type, api_key=SECRET, enabled=1)).insert()

	def new_model(self, account, model_id):
		return frappe.get_doc(dict(doctype="AI Model", model_label=f"{account.name} {model_id}", account=account.name,
			model_id=model_id)).insert().name

	def call(self, **kwargs):
		kwargs.setdefault("deadline", time.monotonic() + 60)
		return agent.call_agent("Verifier", "Verify", "Document", tools=self.tools, schema=SCHEMA,
			phase="Verifier Agent Step", reference_doctype="DocType", reference_name="User", run_id=self.prefix, **kwargs)

	def bodies(self):
		return [entry.kwargs["json"] for entry in self.post.call_args_list]

	def logs(self):
		return frappe.get_all("Document AI Call Log", filters={"run_id": self.prefix}, fields=["*"], order_by="creation asc")

	def test_openrouter_tool_round_trip(self):
		self.post.side_effect = [openrouter_turn([{"text": "hi"}]), openrouter_turn(content=FINAL)]
		result = self.call()
		self.assertEqual(result["data"], {"answer": "ok"})
		self.echo.assert_called_once_with(text="hi")
		first, second = self.bodies()
		self.assertEqual(first["tools"][0]["function"]["name"], "echo")
		self.assertEqual(first["response_format"]["json_schema"]["schema"], SCHEMA)
		self.assertTrue(first["response_format"]["json_schema"]["strict"])
		assistant, tool = second["messages"][2:]
		self.assertEqual(assistant["tool_calls"][0]["id"], "call_0")
		self.assertEqual((tool["role"], tool["tool_call_id"]), ("tool", "call_0"))
		self.assertEqual(json.loads(tool["content"])["echo"], "hi")
		self.assert_redacted(tool["content"])
		logs = self.logs()
		self.assertEqual([log.phase for log in logs], ["Verifier Agent Step"] * 2)
		self.assertFalse(SECRET in "".join(log.request_payload + log.response_payload for log in logs))

	def test_codex_tool_round_trip_resends_full_input(self):
		self.account.update(dict(provider="OpenAI Codex", auth_type="ChatGPT Login"))
		self.account.save()
		credentials = dict(access_token="local-only-access", refresh_token="local-only-refresh", chatgpt_account_id="local")
		self.enterContext(patch.object(client.accounts, "credentials", return_value=credentials))
		self.post.side_effect = [codex_turn([{"text": "hi"}]), codex_turn(content=FINAL)]
		self.assertEqual(self.call()["data"], {"answer": "ok"})
		first, second = self.bodies()
		self.assertEqual((first["stream"], first["store"]), (True, False))
		self.assertEqual(first["tools"][0]["name"], "echo")
		self.assertTrue(first["text"]["format"]["strict"])
		user, call, output = second["input"]
		self.assertEqual(user, first["input"][0])
		self.assertEqual(call, {"type": "function_call", "call_id": "call_0", "name": "echo", "arguments": '{"text": "hi"}'})
		self.assertEqual((output["type"], output["call_id"]), ("function_call_output", "call_0"))
		self.assert_redacted(output["output"])

	def test_anthropic_tool_round_trip(self):
		self.account.update(dict(provider="Anthropic"))
		self.account.save()
		self.post.side_effect = [anthropic_turn([{"text": "hi"}]), anthropic_turn(content=FINAL)]
		self.assertEqual(self.call()["data"], {"answer": "ok"})
		first, second = self.bodies()
		self.assertEqual(first["tools"][0]["input_schema"]["properties"]["text"], {"type": "string"})
		self.assertEqual(first["output_config"]["format"]["schema"], SCHEMA)
		self.assertEqual(first["tool_choice"], {"type": "auto"})
		assistant, results = second["messages"][1:]
		self.assertEqual(assistant["content"][0]["type"], "tool_use")
		self.assertEqual(results["content"][0]["tool_use_id"], "toolu_0")
		self.assert_redacted(results["content"][0]["content"])

	def assert_redacted(self, text):
		self.assertFalse(SECRET in text)
		value = json.loads(text)
		self.assertEqual(value["secret"], "<redacted>")
		self.assertEqual(value["note"], "has <redacted>")

	def test_twelve_tool_calls_then_final_turn_without_tools(self):
		five = [{"text": str(index)} for index in range(5)]
		self.post.side_effect = [openrouter_turn(five), openrouter_turn(five), openrouter_turn(five),
			openrouter_turn(content=FINAL)]
		self.call()
		self.assertEqual(self.echo.call_count, agent.MAX_TOOL_CALLS)
		bodies = self.bodies()
		self.assertEqual([body["tool_choice"] for body in bodies], ["auto", "auto", "auto", "none"])
		refused = [message for message in bodies[3]["messages"] if message["role"] == "tool"][-3:]
		self.assertTrue(all(agent.LIMIT_REACHED in message["content"] for message in refused))

	def test_tool_calls_after_the_limit_fail(self):
		run = agent.AgentRun(self.tools, [], time.monotonic() + 60, max_tool_calls=0)
		self.post.return_value = openrouter_turn([{"text": "again"}])
		outcome = run.run_route(self.account, "row-model", ("Verify", "Document", SCHEMA),
			client.route_options(client.task_options(self.settings), frappe._dict(max_tokens=100, temperature=0)),
			dict.fromkeys(CONTEXT_KEYS))
		self.assertFalse(outcome["ok"])
		self.assertIn("after the limit", outcome["error"])
		self.echo.assert_not_called()

	def test_deadline_stops_before_a_turn(self):
		with self.assertRaises(TimeoutError):
			self.call(deadline=time.monotonic() - 1)
		self.post.assert_not_called()

	def test_turn_retries_are_bounded_and_stop_at_the_deadline(self):
		self.settings.max_attempts = 3
		now = [1000.0]
		self.post.return_value = response(status=503)
		with (
			patch.object(client.time, "monotonic", side_effect=lambda: now[0]),
			patch.object(client.time, "sleep", side_effect=lambda seconds: now.__setitem__(0, now[0] + 4)),
			self.assertRaises(client.DeadlineReached),
		):
			self.call(deadline=now[0] + 5)
		self.assertEqual([entry.kwargs["timeout"] for entry in self.post.call_args_list], [5, 1])

	def test_model_override_first_then_task_rows(self):
		chosen = self.new_account("OpenRouter", "API Key", "-chosen")
		self.post.side_effect = [response(status=400), openrouter_turn(content=FINAL)]
		self.call(ai_model=self.new_model(chosen, "chosen-model"))
		self.assertEqual([body["model"] for body in self.bodies()], ["chosen-model", "row-model"])
		self.post.reset_mock()
		self.post.side_effect = [openrouter_turn(content=FINAL)]
		self.call()
		self.assertEqual([body["model"] for body in self.bodies()], ["row-model"])

	def test_model_override_without_task_rows_tries_only_the_override(self):
		self.settings.task_models = []
		self.post.return_value = response(status=400)
		with self.assertRaises(frappe.ValidationError):
			self.call(ai_model=self.new_model(self.account, "chosen-model"))
		self.assertEqual([body["model"] for body in self.bodies()], ["chosen-model"])

	def test_tool_errors_unknown_tools_and_bad_arguments_go_back_to_the_model(self):
		run = agent.AgentRun(self.tools, [SECRET], time.monotonic() + 60)
		self.echo.side_effect = frappe.PermissionError(f"No access {SECRET}")
		cases = [
			({"name": "echo", "arguments": "{}"}, "No access <redacted>"),
			({"name": "run_sql", "arguments": "{}"}, "Unknown tool run_sql"),
			({"name": "echo", "arguments": "not json"}, "Expecting value"),
			({"name": "echo", "arguments": "[1]"}, "JSON object"),
		]
		for call, error in cases:
			with self.subTest(call=call):
				self.assertIn(error, json.loads(run.run_tool(call))["error"])

	def test_tool_result_is_capped(self):
		self.echo.side_effect = lambda text="": "x" * 50000
		run = agent.AgentRun(self.tools, [], time.monotonic() + 60)
		text = run.run_tool({"name": "echo", "arguments": {}})
		self.assertEqual(len(text), agent.MAX_RESULT_CHARS)
		self.assertTrue(text.endswith(agent.TRUNCATED))

	def test_known_secrets_include_accounts_settings_and_site_config(self):
		with patch.dict(frappe.conf, {"db_password": "local-db-password"}):
			secrets = agent.get_known_secrets(self.settings)
		self.assertIn(SECRET, secrets)
		self.assertIn("local-db-password", secrets)
		self.assertIn(frappe.conf.encryption_key, secrets)
		self.assertNotIn(None, secrets)

	def test_user_prompt_is_redacted_before_sending(self):
		self.post.side_effect = [openrouter_turn(content=FINAL)]
		agent.call_agent("Verifier", "Verify", f"Document {SECRET}", tools=self.tools, schema=SCHEMA,
			deadline=time.monotonic() + 60, run_id=self.prefix)
		self.assertFalse(SECRET in json.dumps(self.bodies()[0]))

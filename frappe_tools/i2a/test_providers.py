import json
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from frappe_tools.ai import client
from frappe_tools.ai.test_providers import IMAGE, response
from frappe_tools.i2a import providers

SECRET = "local-only-secret-key"
TOOL = {"type": "function", "function": {"name": "find", "description": "Find", "parameters": {"type": "object"}}}
CODEX_CREDENTIALS = dict(access_token="local-only-access", refresh_token="local-only-refresh", chatgpt_account_id="local")


def openrouter_reply(content, finish="stop", usage=None):
	return response({"choices": [{"message": {"content": content}, "finish_reason": finish}], "usage": usage or {}})


def codex_reply(calls=(), content=""):
	events = [
		{"type": "response.output_item.done", "item": {"type": "function_call", "call_id": call_id, "name": "find",
			"arguments": '{"q": "x"}'}}
		for call_id in calls
	]
	if content:
		events.append({"type": "response.output_text.delta", "delta": content})
	events.append({"type": "response.completed", "response": {"output": [], "usage": {"input_tokens": 3, "output_tokens": 2}}})
	return response(events=events)


class TestI2AProviders(UnitTestCase):
	def setUp(self):
		self._outcome.result.tb_locals = False
		frappe.set_user("Administrator")
		self.prefix = "LOCAL-I2A-" + frappe.generate_hash(length=10)
		self.account = frappe.get_doc(dict(doctype="AI Provider Account", account_name=self.prefix, provider="OpenRouter",
			auth_type="API Key", api_key=SECRET, enabled=1)).insert()
		self.model = frappe.get_doc(dict(doctype="AI Model", model_label=self.prefix, account=self.account.name,
			model_id="local/model", supports_json_mode=1, max_tokens=8192, cost_per_m_input=1, cost_per_m_output=2)).insert()
		self.post = self.enterContext(patch("requests.post", return_value=openrouter_reply('{"hello": 1}')))
		self.sleep = self.enterContext(patch.object(providers.time, "sleep"))

	def tearDown(self):
		frappe.db.rollback()

	def use_codex(self):
		self.account.update(dict(provider="OpenAI Codex", auth_type="ChatGPT Login"))
		self.account.save()
		self.enterContext(patch.object(client.accounts, "credentials", return_value=CODEX_CREDENTIALS))

	def logs(self):
		return frappe.get_all("I2A LLM Call", filters={"ai_model": self.model.name}, fields=["*"], order_by="creation asc")

	def bodies(self):
		return [entry.kwargs["json"] for entry in self.post.call_args_list]

	def test_openrouter_call_logs_one_row_with_rate_card_cost(self):
		self.post.return_value = openrouter_reply('{"hello": 1}', usage={"prompt_tokens": 1000, "completion_tokens": 500})
		out = providers.call_model(self.model.name, [{"role": "system", "content": "Rules"}, {"role": "user", "content": "hi"}],
			purpose="extract")
		self.assertEqual(out["data"], {"hello": 1})
		body = self.bodies()[0]
		self.assertEqual(body["messages"][0], {"role": "system", "content": "Rules"})
		self.assertEqual(body["response_format"], {"type": "json_object"})
		(log,) = self.logs()
		self.assertEqual((log.provider, log.status, log.cost_estimated), ("OpenRouter", "Success", 1))
		self.assertAlmostEqual(log.cost_usd, 0.002)

	def test_json_mode_follows_the_model(self):
		self.model.db_set("supports_json_mode", 0)
		providers.call_model(self.model.name, [{"role": "user", "content": "hi"}], purpose="extract")
		self.assertNotIn("response_format", self.bodies()[0])

	def test_truncated_reply_is_asked_again_with_more_tokens(self):
		self.post.side_effect = [openrouter_reply('{"partial": ', "length"), openrouter_reply('{"hello": 1}')]
		out = providers.call_model(self.model.name, [{"role": "user", "content": "hi"}], purpose="extract")
		self.assertEqual(out["data"], {"hello": 1})
		self.assertEqual([body["max_tokens"] for body in self.bodies()], [8192, providers.TRUNCATION_RETRY_MAX_TOKENS])
		self.assertEqual([log.status for log in self.logs()], ["Error", "Success"])

	def test_server_errors_retry_and_client_errors_stop(self):
		self.post.side_effect = [response(status=503), openrouter_reply('{"hello": 1}')]
		self.assertEqual(providers.call_model(self.model.name, [{"role": "user", "content": "hi"}])["data"], {"hello": 1})
		self.post.reset_mock()
		self.post.side_effect = [response({"error": {"message": "bad"}}, status=400)]
		with self.assertRaisesRegex(providers.ProviderError, "bad"):
			providers.call_model(self.model.name, [{"role": "user", "content": "hi"}])
		self.assertEqual(self.post.call_count, 1)

	def test_disabled_model_or_account_is_refused(self):
		self.account.db_set("enabled", 0)
		with self.assertRaisesRegex(providers.ProviderError, "Account"):
			providers.call_model(self.model.name, [])
		self.model.db_set("enabled", 0)
		with self.assertRaisesRegex(providers.ProviderError, "AI Model"):
			providers.call_with_tools(self.model.name, [], [TOOL])
		self.post.assert_not_called()

	def test_usable_needs_an_enabled_model_and_account(self):
		self.assertTrue(self.model.is_usable)
		self.account.db_set("enabled", 0)
		self.assertFalse(self.model.is_usable)

	def test_incomplete_codex_reply_logs_its_usage(self):
		self.use_codex()
		usage = {"input_tokens": 1000, "output_tokens": 500}
		incomplete = response(events=[{"type": "response.incomplete", "response": {"usage": usage}}])
		self.post.side_effect = [incomplete, codex_reply(content='{"hello": 1}')]
		self.assertEqual(providers.call_model(self.model.name, [{"role": "user", "content": "hi"}])["data"], {"hello": 1})
		log = self.logs()[0]
		self.assertEqual((log.status, log.prompt_tokens, log.completion_tokens), ("Error", 1000, 500))
		self.assertAlmostEqual(log.cost_usd, 0.002)

	def test_codex_tool_loop_keeps_the_openai_chat_shape(self):
		self.use_codex()
		self.post.side_effect = [codex_reply(["call_1"]), codex_reply(content="All done")]
		messages = [{"role": "system", "content": "Rules"}, {"role": "user", "content": "Resolve"}]
		first = providers.call_with_tools(self.model.name, messages, [TOOL], purpose="agent")
		self.assertEqual(first["tool_calls"], [{"id": "call_1", "name": "find", "arguments": {"q": "x"}}])
		messages += [first["message"], {"role": "tool", "tool_call_id": "call_1", "content": "found"}]
		second = providers.call_with_tools(self.model.name, messages, [TOOL], purpose="agent")
		self.assertEqual((second["content"], second["tool_calls"]), ("All done", []))
		body = self.bodies()[1]
		self.assertEqual(body["instructions"], "Rules")
		self.assertEqual(body["tools"][0]["name"], "find")
		self.assertEqual([item.get("type") for item in body["input"][1:]], ["function_call", "function_call_output"])
		self.assertEqual([log.provider for log in self.logs()], ["OpenAI Codex"] * 2)

	def test_truncated_tool_reply_fails_without_a_new_ask(self):
		self.post.return_value = response({"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]})
		with self.assertRaisesRegex(providers.ProviderError, "truncated"):
			providers.call_with_tools(self.model.name, [{"role": "user", "content": "hi"}], [TOOL])
		self.assertEqual(self.post.call_count, 1)

	def test_anthropic_reads_images_and_logs_no_image_or_secret(self):
		self.account.update(dict(provider="Anthropic"))
		self.account.save()
		self.post.return_value = response({"content": [{"type": "text", "text": json.dumps({"echo": SECRET})}],
			"stop_reason": "end_turn", "usage": {"input_tokens": 2, "output_tokens": 1}})
		content = [{"type": "text", "text": "Read"}, {"type": "image_url", "image_url": {"url": IMAGE}}]
		providers.call_model(self.model.name, [{"role": "system", "content": "Rules"}, {"role": "user", "content": content}])
		body = self.bodies()[0]
		self.assertEqual(body["messages"][0]["content"][1]["source"]["media_type"], "image/png")
		self.assertTrue(body["system"][0]["text"].startswith("Rules"))
		(log,) = self.logs()
		self.assertFalse(SECRET in log.request_payload + log.response_payload)
		self.assertFalse("bG9jYWw=" in log.request_payload)

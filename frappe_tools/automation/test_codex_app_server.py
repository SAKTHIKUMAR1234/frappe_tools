import json
import time
from unittest import TestCase
from unittest.mock import Mock, patch

from frappe_tools.automation.codex_app_server import (
	CodexAppServerClient,
	_bounded_json_text,
	_normalize_usage,
	_restricted_thread_config,
	dynamic_tool_specs,
)


class TestCodexAppServerClient(TestCase):
	@patch.object(CodexAppServerClient, "request")
	def test_structured_turn_forwards_native_local_image_inputs(self, request):
		client = CodexAppServerClient("/bin/false", cwd="/tmp", environment={}, timeout=1)
		request.side_effect = [
			{"config": {"mcp_servers": {}}},
			{"thread": {"id": "thread-1"}},
			{},
		]
		client._read_message = Mock(side_effect=[
			{"method": "item/completed", "params": {"item": {
				"type": "agentMessage", "phase": "final_answer", "text": '{"json":"{}"}',
			}}},
			{"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
		])

		result = client.run_structured(
			prompt="extract",
			output_schema={"type": "object"},
			model="gpt-test",
			dynamic_tools=[],
			input_items=[
				{"type": "text", "text": "extract"},
				{"type": "localImage", "path": "/tmp/page-1.png"},
			],
		)

		turn = request.call_args_list[2]
		self.assertEqual(turn.args[0], "turn/start")
		self.assertEqual(turn.args[1]["input"][1]["type"], "localImage")
		self.assertEqual(result["text"], '{"json":"{}"}')

	def test_converts_openai_tool_schema_to_app_server_dynamic_tool(self):
		tools = dynamic_tool_specs([{
			"type": "function",
			"function": {
				"name": "find_invoice",
				"description": "Find a local invoice",
				"parameters": {"type": "object", "properties": {"number": {"type": "string"}}},
			},
		}])

		self.assertEqual(tools[0]["name"], "find_invoice")
		self.assertEqual(tools[0]["inputSchema"]["properties"]["number"]["type"], "string")

	def test_buffered_messages_are_consumed_without_waiting_for_more_fd_activity(self):
		client = CodexAppServerClient("/bin/false", cwd="/tmp", environment={}, timeout=1)
		client.process = Mock()
		client.process.poll.return_value = None
		client._stdout_lines.put(json.dumps({"jsonrpc": "2.0", "method": "item/completed"}))
		client._stdout_lines.put(json.dumps({"jsonrpc": "2.0", "method": "turn/completed"}))

		first = client._read_message(time.monotonic() + 0.1)
		second = client._read_message(time.monotonic() + 0.1)

		self.assertEqual(first["method"], "item/completed")
		self.assertEqual(second["method"], "turn/completed")

	def test_dynamic_tool_result_is_fail_closed_and_audited(self):
		client = CodexAppServerClient("/bin/false", cwd="/tmp", environment={}, timeout=1)
		client._send = Mock()
		trace = []
		client._handle_dynamic_tool(
			{"id": 7, "params": {"tool": "unknown", "arguments": {"q": "x"}}},
			lambda name, arguments: {"error": "unknown tool"},
			trace,
		)

		self.assertFalse(trace[0]["ok"])
		self.assertFalse(client._send.call_args.args[0]["result"]["success"])

	def test_usage_is_normalized_to_existing_audit_contract(self):
		self.assertEqual(
			_normalize_usage({"inputTokens": 10, "outputTokens": 2, "totalTokens": 12, "cachedInputTokens": 7}),
			{"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "cached_tokens": 7},
		)

	def test_restricted_thread_disables_inherited_mcp_and_native_tools(self):
		config = _restricted_thread_config(["codex_apps", "user_server"])

		self.assertFalse(config["features.shell_tool"])
		self.assertFalse(config["features.multi_agent"])
		self.assertFalse(config["features.chronicle"])
		self.assertFalse(config["features.view_image"])
		self.assertFalse(config["agents.enabled"])
		self.assertFalse(config["tools.update_plan.enabled"])
		self.assertEqual(config["web_search"], "disabled")
		self.assertEqual(config["mcp_servers"]["codex_apps"], {"enabled": False})
		self.assertEqual(config["mcp_servers"]["user_server"], {"enabled": False})

	def test_oversized_tool_result_remains_valid_json(self):
		result = json.loads(_bounded_json_text({"rows": ["x" * 9000]}))

		self.assertTrue(result["truncated"])
		self.assertGreater(result["original_characters"], 8000)

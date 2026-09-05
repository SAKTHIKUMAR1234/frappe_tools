import base64
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from frappe_tools.automation import agent_providers
from frappe_tools.i2a import providers as api_providers


def _model(**overrides):
	values = {
		"name": "Document Automation Luna",
		"model_id": "gpt-5.6-luna",
		"provider": "Codex OAuth",
		"enabled": 1,
		"agent_executable": None,
		"agent_timeout_seconds": 60,
		"agent_max_concurrency": 2,
		"agent_queue_timeout_seconds": 1,
		"fallback_model": None,
		"supports_vision": 1,
	}
	values.update(overrides)
	return SimpleNamespace(**values)


class TestSubscriptionAgentProviders(TestCase):
	def test_decision_turn_is_normalized_to_existing_runtime_contract(self):
		result = agent_providers._normalize_turn({
			"kind": "decision",
			"tool_calls": [],
			"decision": {
				"status": "review",
				"confidence": 0.97,
				"values_json": '{"supplier":"SUP-1"}',
				"reasons": ["local match"],
				"handoff_reasons": [],
				"memory_sources": ["MEM-1"],
			},
		})

		self.assertEqual(result["tool_calls"], [])
		self.assertEqual(json.loads(result["content"])["values"]["supplier"], "SUP-1")

	def test_tool_turn_only_returns_declared_request_shape(self):
		result = agent_providers._normalize_turn({
			"kind": "tool_calls",
			"tool_calls": [{"id": "call-1", "name": "search_suppliers", "arguments_json": '{"query":"Vest"}'}],
			"decision": {"status": "handoff", "confidence": 0, "values_json": "{}", "reasons": [],
				"handoff_reasons": [], "memory_sources": []},
		})

		self.assertEqual(result["tool_calls"][0]["arguments"], {"query": "Vest"})
		self.assertEqual(result["finish_reason"], "tool_calls")

	@patch("frappe_tools.automation.agent_providers._log_agent_call")
	@patch("frappe_tools.automation.agent_providers._resolve_executable", return_value="/usr/bin/codex")
	@patch("frappe_tools.automation.agent_providers.CodexAppServerClient")
	def test_codex_oauth_uses_app_server_schema_constrained_turn(self, client_class, _executable, log_call):
		client = client_class.return_value.__enter__.return_value
		client.run_structured.return_value = {
			"text": json.dumps({
				"kind": "decision",
				"tool_calls": [],
				"decision": {"status": "review", "confidence": 0.91, "values_json": "{}",
					"reasons": ["bounded evidence"], "handoff_reasons": [], "memory_sources": []},
			}),
			"usage": {"prompt_tokens": 100, "cached_tokens": 60, "completion_tokens": 20, "total_tokens": 120},
			"tool_trace": [],
			"thread_id": "thread-1",
		}
		with tempfile.TemporaryDirectory() as root:
			with patch("frappe_tools.automation.agent_providers._work_root", return_value=root):
				result = agent_providers.CodexOAuthAgent().call_with_tools(
					_model(), [{"role": "user", "content": "decide"}], [], purpose="document_decision",
				)

		client_class.assert_called_once()
		client.run_structured.assert_called_once()
		self.assertEqual(client.run_structured.call_args.kwargs["output_schema"], agent_providers._TURN_SCHEMA)
		self.assertEqual(result["usage"]["total_tokens"], 120)
		self.assertEqual(result["transport_provider"], "Codex OAuth")
		self.assertEqual(result["agent_thread_id"], "thread-1")
		log_call.assert_called_once()

	@patch("frappe_tools.automation.agent_providers._log_agent_call")
	@patch("frappe_tools.automation.agent_providers._resolve_executable", return_value="/usr/bin/codex")
	@patch("frappe_tools.automation.agent_providers.CodexAppServerClient")
	def test_codex_vision_materializes_native_local_image_and_parses_envelope(
		self, client_class, _executable, log_call
	):
		client = client_class.return_value.__enter__.return_value
		seen = {}
		def run_structured(**kwargs):
			seen["items"] = kwargs["input_items"]
			seen["image_existed"] = Path(kwargs["input_items"][1]["path"]).is_file()
			return {
				"text": json.dumps({"json": json.dumps({"fields": [{"fieldname": "supplier", "value": "SUP-1"}]})}),
				"usage": {"total_tokens": 40},
			}
		client.run_structured.side_effect = run_structured
		image = "data:image/png;base64," + base64.b64encode(b"safe-image").decode()
		with tempfile.TemporaryDirectory() as root:
			with patch("frappe_tools.automation.agent_providers._work_root", return_value=root):
				result = agent_providers.call_vision(
					_model(), [image], "extract fields", "read page", run="DOCEXT-1", action="Sales Invoice",
				)
				items = seen["items"]
				self.assertEqual(items[1]["type"], "localImage")
				self.assertTrue(items[1]["path"].endswith("page-001.png"))
				self.assertTrue(seen["image_existed"])

		self.assertEqual(result["data"]["fields"][0]["value"], "SUP-1")
		self.assertFalse(Path(items[1]["path"]).exists())
		self.assertNotIn("safe-image", str(log_call.call_args))

	def test_codex_vision_rejects_any_fallback(self):
		with self.assertRaises(api_providers.ProviderError):
			agent_providers.call_vision(_model(fallback_model="Unsafe"), [], "", "")

	@patch("frappe_tools.automation.agent_providers._log_agent_call")
	@patch("frappe_tools.automation.agent_providers._resolve_executable", return_value="/usr/bin/codex")
	@patch("frappe_tools.automation.agent_providers.CodexAppServerClient")
	def test_invalid_vision_json_fails_closed_and_retains_error_audit(self, client_class, _executable, log_call):
		client = client_class.return_value.__enter__.return_value
		image = "data:image/png;base64," + base64.b64encode(b"safe-image").decode()
		for invalid in ["not json", "[]", "null", "123"]:
			with self.subTest(invalid=invalid):
				client.run_structured.return_value = {"text": json.dumps({"json": invalid}), "usage": {"total_tokens": 10}}
				with tempfile.TemporaryDirectory() as root:
					with patch("frappe_tools.automation.agent_providers._work_root", return_value=root):
						with self.assertRaisesRegex(api_providers.ProviderError, "invalid document JSON"):
							agent_providers.call_vision(_model(), [image], "extract", "read", run="DOCEXT-TEST")
				self.assertEqual(log_call.call_args.kwargs["status"], "Error")
				self.assertIn("invalid document JSON", log_call.call_args.kwargs["error"])

	@patch("frappe_tools.automation.agent_providers.CodexOAuthAgent.call_with_live_tools")
	@patch("frappe_tools.automation.agent_providers.frappe.get_doc")
	def test_live_tools_use_subscription_provider(self, get_doc, live_call):
		get_doc.return_value = _model()
		live_call.return_value = {"content": "{}"}

		result = agent_providers.call_with_live_tools(
			"Document Automation Luna", [], [], lambda name, arguments: {}, purpose="document_decision"
		)

		self.assertEqual(result, {"content": "{}"})
		live_call.assert_called_once()

	@patch("frappe_tools.automation.agent_providers.api_providers.call_with_tools")
	@patch("frappe_tools.automation.agent_providers.CodexOAuthAgent.call_with_tools")
	@patch("frappe_tools.automation.agent_providers.frappe.get_doc")
	def test_fallback_can_switch_from_subscription_agent_to_openrouter(self, get_doc, codex_call, api_call):
		primary = _model(fallback_model="Agent Fallback")
		fallback = _model(name="Agent Fallback", provider="OpenRouter", fallback_model=None)
		get_doc.side_effect = lambda doctype, name: primary if name == primary.name else fallback
		codex_call.side_effect = api_providers.ProviderError("subscription limit reached")
		api_call.return_value = {"content": "{}", "tool_calls": [], "message": {"role": "assistant", "content": "{}"}}

		result = agent_providers.call_with_tools(primary.name, [], [])

		self.assertEqual(result["content"], "{}")
		api_call.assert_called_once()

	def test_safe_environment_never_forwards_api_key(self):
		with patch.dict("os.environ", {"HOME": "/srv/frappe", "PATH": "/bin", "OPENAI_API_KEY": "secret"}, clear=True):
			environment = agent_providers._safe_environment("/storage/tmp")

		self.assertNotIn("OPENAI_API_KEY", environment)
		self.assertEqual(environment["TMPDIR"], "/storage/tmp")

	def test_configured_agent_executable_has_highest_priority(self):
		with tempfile.TemporaryDirectory() as root:
			real = Path(root) / "codex"
			real.write_text("#!/bin/sh\n", encoding="utf-8")
			real.chmod(0o700)
			with patch.dict("os.environ", {"C3_CODEX_REAL": str(real)}, clear=False):
				self.assertEqual(
					agent_providers._resolve_executable(_model(agent_executable=str(real)), "codex"), str(real)
				)

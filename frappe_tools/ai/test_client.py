import json
import time
from unittest.mock import patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from rq.timeouts import JobTimeoutException

from frappe_tools.ai import client
from frappe_tools.ai.test_providers import IMAGE, SCHEMA, response
from frappe_tools.utils import llm


class TestClient(FrappeTestCase):
	def setUp(self):
		self._outcome.result.tb_locals = False
		frappe.set_user("Administrator")
		self.prefix = "LOCAL-AI-" + frappe.generate_hash(length=10)
		self.account = frappe.get_doc(
			dict(
				doctype="AI Provider Account",
				account_name=self.prefix,
				provider="OpenRouter",
				auth_type="API Key",
				api_key="local-only-secret-key",
				enabled=1,
			)
		).insert()
		self.settings = frappe.get_single("Document Extraction Settings")
		self.settings.update(
			dict(
				enable=1,
				model="local-legacy-model",
				max_attempts=2,
				request_timeout=5,
				max_tokens=100,
				temperature=0.25,
				task_models=[],
			)
		)
		self.ensure_ready = llm.ensure_ready
		self.enterContext(patch.object(llm, "ensure_ready", return_value=self.settings))
		self.enterContext(patch.object(self.settings, "get_api_key", return_value="local-legacy-secret"))
		self.sleep = self.enterContext(patch.object(client.time, "sleep"))
		self.post = self.enterContext(patch("requests.post", return_value=self.success()))

	def tearDown(self):
		frappe.db.rollback()

	def success(self):
		return response(
			{
				"choices": [{"message": {"content": '{"quantity":12}'}, "finish_reason": "stop"}],
				"usage": {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13, "cost": 0.03},
			}
		)

	def row(self, model, **kwargs):
		self.settings.append(
			"task_models",
			dict(task="Scan Decision", account=self.account.name, model=model, enabled=1, **kwargs),
		)

	def call(self, **kwargs):
		return client.call_model("Scan Decision", "Extract", "Quantity", run_id=self.prefix, **kwargs)

	def logs(self):
		return frappe.get_all(
			"Document AI Call Log", filters={"run_id": self.prefix}, fields=["*"], order_by="creation asc"
		)

	def test_legacy_fallback_and_wrapper(self):
		result = llm.call_vision(
			[IMAGE], "Extract", "Quantity", task="Scan Decision", run_id=self.prefix, min_output_tokens=16000
		)
		self.assertEqual(result["model"], "OpenRouter/local-legacy-model")
		self.assertEqual(result["data"], {"quantity": 12})
		self.assertEqual(self.post.call_args.kwargs["json"]["max_tokens"], 16000)
		self.assertEqual(self.logs()[0].cost_usd, 0.03)

	def test_fallback_order_and_attempt_logs(self):
		self.row("first")
		self.row("second")
		self.post.side_effect = [response(status=503), response(status=503), self.success()]
		self.assertEqual(self.call()["model"], "OpenRouter/second")
		self.assertEqual(
			[c.kwargs["json"]["model"] for c in self.post.call_args_list], ["first", "first", "second"]
		)
		self.assertEqual([log.status for log in self.logs()], ["Error", "Error", "Success"])

	def test_deadline_bounds_requests_and_stops_fallback(self):
		self.row("first")
		self.row("second")
		clock = time.monotonic
		deadline = clock() + 30
		self.post.side_effect = [response(status=503), self.success()]
		# The budget is used up during the wait before the retry.
		with (
			patch.object(client.time, "monotonic", side_effect=lambda: deadline + 1 if self.sleep.called else clock()),
			self.assertRaises(client.DeadlineReached),
		):
			self.call(deadline=deadline)
		self.assertEqual(self.post.call_count, 1)
		self.assertLessEqual(self.post.call_args.kwargs["timeout"], 30)

	def test_each_request_step_gets_only_the_time_left(self):
		self.row("first")
		self.settings.request_timeout = 120
		now = [1000.0]

		def slow_credentials(account, timeout):
			now[0] += 7
			return {"api_key": "local-only-secret-key"}

		with (
			patch.object(client.time, "monotonic", side_effect=lambda: now[0]),
			patch.object(client.accounts, "credentials", side_effect=slow_credentials) as credentials,
		):
			self.call(deadline=now[0] + 10)
		self.assertEqual(credentials.call_args.args[1], 10)
		self.assertEqual(self.post.call_args.kwargs["timeout"], 3)

	def test_stream_stops_reading_at_the_deadline(self):
		self.row("chosen")
		self.account.update(dict(provider="OpenAI Codex", auth_type="ChatGPT Login"))
		self.account.save()
		credentials = dict(access_token="local-only-access", refresh_token="local-only-refresh", chatgpt_account_id="local-account")
		now = [1000.0]
		deadline = now[0] + 30

		def slow_stream():
			delta = {"type": "response.output_text.delta", "delta": "{}"}
			while True:
				now[0] += 20
				yield b"data: " + json.dumps(delta).encode()

		stream = response()
		stream.iter_lines.return_value = slow_stream()
		self.post.return_value = stream
		with (
			patch.object(client.time, "monotonic", side_effect=lambda: now[0]),
			patch.object(client.accounts, "credentials", return_value=credentials),
			self.assertRaises(client.DeadlineReached),
		):
			self.call(deadline=deadline)
		self.assertEqual(self.post.call_count, 1)
		self.assertEqual([log.status for log in self.logs()], ["Timeout"])
		stream.close.assert_called_once()

	def test_slowly_sent_answer_stops_at_the_deadline(self):
		self.row("chosen")
		now = [1000.0]

		def slow_body():
			while True:
				now[0] += 20
				yield b" "

		slow = self.success()
		slow.iter_content.return_value = slow_body()
		self.post.return_value = slow
		with patch.object(client.time, "monotonic", side_effect=lambda: now[0]), self.assertRaises(client.DeadlineReached):
			self.call(deadline=now[0] + 30)
		self.assertTrue(self.post.call_args.kwargs["stream"])
		self.assertEqual(self.post.call_count, 1)
		self.assertEqual([log.status for log in self.logs()], ["Timeout"])

	def test_answer_completed_after_the_deadline_is_rejected(self):
		self.row("chosen")
		self.settings.max_attempts = 1
		now = [1000.0]
		late = self.success()
		body = late.iter_content.return_value[0]

		def last_part_arrives_late():
			yield body
			now[0] += 40

		late.iter_content.return_value = last_part_arrives_late()
		self.post.return_value = late
		with patch.object(client.time, "monotonic", side_effect=lambda: now[0]), self.assertRaises(client.DeadlineReached):
			self.call(deadline=now[0] + 30)
		self.assertEqual([log.status for log in self.logs()], ["Timeout"])

	def test_budget_ending_on_the_last_attempt_is_a_deadline_not_a_failure(self):
		self.row("only")
		self.settings.max_attempts = 1
		now = [1000.0]

		def timed_out(*args, **kwargs):
			now[0] += 30
			raise requests.Timeout("Local read timeout")

		self.post.side_effect = timed_out
		with patch.object(client.time, "monotonic", side_effect=lambda: now[0]), self.assertRaises(client.DeadlineReached):
			self.call(deadline=now[0] + 10)
		self.assertEqual([log.status for log in self.logs()], ["Timeout"])

	def test_unauthorized_skips_retries(self):
		self.row("first")
		self.row("second")
		for status in (401, 403):
			self.post.reset_mock()
			self.post.side_effect = [response(status=status), self.success()]
			self.assertEqual(self.call()["model"], "OpenRouter/second")
			self.assertEqual(self.post.call_count, 2)
		self.sleep.assert_not_called()

	def test_disabled_rows_and_accounts_skipped(self):
		self.row("disabled-row")
		self.settings.task_models[-1].enabled = 0
		other = frappe.copy_doc(self.account)
		other.account_name = self.prefix + "-disabled"
		other.enabled = 0
		other.insert()
		self.row("disabled-account")
		self.settings.task_models[-1].account = other.name
		self.row("enabled")
		self.assertEqual(self.call()["model"], "OpenRouter/enabled")
		self.assertEqual(self.post.call_count, 1)

	def test_disabled_task_does_not_use_legacy(self):
		self.row("disabled")
		self.settings.task_models[0].enabled = 0
		with self.assertRaises(frappe.ValidationError):
			self.call()
		self.post.assert_not_called()

	def test_bad_json_and_timeout_retry(self):
		self.row("first")
		for failure in (response({"choices": []}), requests.Timeout("local timeout"), response(status=429)):
			self.post.reset_mock()
			self.post.side_effect = [failure, self.success()]
			self.assertEqual(self.call()["data"], {"quantity": 12})
			self.assertEqual(self.post.call_count, 2)

	def test_all_fail_and_job_timeout_propagates(self):
		self.row("first")
		self.post.return_value = response(status=400)
		with self.assertRaisesRegex(frappe.ValidationError, "HTTP 400"):
			self.call()
		self.post.side_effect = JobTimeoutException
		with self.assertRaises(JobTimeoutException):
			self.call()

	def test_http_error_body_logged_and_message_redacted(self):
		self.row("chosen")
		secret = "local-only-secret-key"
		for error in ({"message": "Invalid key " + secret}, "Invalid key " + secret):
			with self.subTest(error_type=type(error).__name__):
				self.post.return_value = response({"error": error, "api_key": secret}, status=401)
				with self.assertRaises(frappe.ValidationError) as caught:
					self.call()
				self.assertIn("Invalid key <redacted>", str(caught.exception))
				self.assertFalse(secret in str(caught.exception))
				log = self.logs()[-1]
				self.assertEqual(log.http_status, 401)
				self.assertIn("Invalid key <redacted>", log.response_payload)
				self.assertIn("Invalid key", log.error_message)
				self.assertFalse(secret in log.response_payload + log.error_message)

	def test_http_text_body_truncated_and_redacted(self):
		self.row("chosen")
		failed = response(status=403)
		failed.json.side_effect = ValueError("Not JSON")
		failed.text = "local-only-secret-key " + "x" * 5000
		self.post.return_value = failed
		with self.assertRaisesRegex(frappe.ValidationError, "HTTP 403"):
			self.call()
		body = json.loads(self.logs()[0].response_payload)["non_json_response"]
		self.assertEqual(len(body), 4000)
		self.assertTrue(body.startswith("<redacted> "))
		self.assertFalse("local-only-secret-key" in body)

	def test_provider_error_messages_limited_after_redaction(self):
		self.row("chosen")
		self.post.return_value = response({"error": "local-only-secret-key " + "x" * 700}, status=400)
		with self.assertRaises(frappe.ValidationError) as caught:
			self.call()
		message = str(caught.exception).removeprefix("AI call failed: ")
		self.assertEqual(len(message), 500)
		self.assertTrue(message.startswith("<redacted> "))

	def test_provider_error_bodies_logged_on_successful_http(self):
		self.row("chosen")
		self.settings.max_attempts = 1
		for provider in ("OpenRouter", "Anthropic"):
			with self.subTest(provider=provider):
				self.account.provider = provider
				self.account.save()
				self.post.return_value = response({"error": {"message": "Rejected local-only-secret-key"}})
				with self.assertRaisesRegex(frappe.ValidationError, "Rejected <redacted>"):
					self.call()
				log = self.logs()[-1]
				self.assertEqual(json.loads(log.response_payload)["error"]["message"], "Rejected <redacted>")

	def test_json_failure_snippet_and_token_hint(self):
		self.row("chosen")
		self.settings.max_attempts = 1
		raw = "invalid local-only-secret-key " + "x" * 250
		for finish in ("stop", "length", "max_tokens", "incomplete"):
			with self.subTest(finish=finish):
				self.post.return_value = response({"choices": [{"message": {"content": raw}, "finish_reason": finish}]})
				with self.assertRaises(frappe.ValidationError) as caught:
					self.call()
				message = str(caught.exception)
				self.assertIn("could not parse JSON", message)
				self.assertEqual(message.split("Raw begins: ")[1], llm.redact_secrets(raw, ["local-only-secret-key"])[:200])
				self.assertEqual("Max Tokens" in message, finish != "stop")
				self.assertFalse("local-only-secret-key" in message)

	def test_exception_detail_is_redacted(self):
		self.row("chosen")
		self.settings.max_attempts = 1
		for error in (requests.Timeout("Timed out local-only-secret-key"), RuntimeError("Transport broke local-only-secret-key")):
			with self.subTest(error_type=type(error).__name__):
				self.post.side_effect = error
				with self.assertRaises(frappe.ValidationError) as caught:
					self.call()
				self.assertIn(str(error).replace("local-only-secret-key", "<redacted>"), str(caught.exception))
				self.assertFalse("local-only-secret-key" in self.logs()[-1].error_message)

	def test_codex_error_events_logged_with_detail_and_incomplete_hint(self):
		self.row("chosen")
		self.settings.max_attempts = 1
		self.account.update(dict(provider="OpenAI Codex", auth_type="ChatGPT Login"))
		self.account.save()
		credentials = dict(access_token="local-only-access", refresh_token="local-only-refresh", chatgpt_account_id="local-account")
		with patch.object(client.accounts, "credentials", return_value=credentials):
			for kind in ("error", "response.failed", "response.incomplete"):
				with self.subTest(kind=kind):
					error = {"error": {"message": "Provider failed local-only-access"}}
					event = {"type": kind, **(error if kind == "error" else {"response": error})}
					self.post.return_value = response(events=[{"type": "response.output_text.delta", "delta": "partial JSON"}, event])
					with self.assertRaises(frappe.ValidationError) as caught:
						self.call()
					message = str(caught.exception)
					self.assertIn("Provider failed <redacted>", message)
					self.assertEqual("Max Tokens" in message, kind == "response.incomplete")
					if kind == "response.incomplete":
						self.assertIn("Raw begins: partial JSON", message)
					log = self.logs()[-1]
					self.assertIn("Provider failed <redacted>", log.response_payload)
					self.assertFalse("local-only-access" in log.response_payload + log.error_message + message)

	def test_schema_and_no_legacy_key_required_for_routes(self):
		self.settings.get_api_key.return_value = None
		self.row("chosen")
		self.call(schema=SCHEMA)
		self.assertEqual(
			self.post.call_args.kwargs["json"]["response_format"]["json_schema"]["schema"], SCHEMA
		)
		self.settings.task_models = []
		with self.assertRaisesRegex(frappe.ValidationError, "OpenRouter API Key"):
			self.call()

	def test_enable_still_required(self):
		with (
			patch.object(llm, "get_settings", return_value=frappe._dict(enable=0)),
			self.assertRaises(frappe.ValidationError),
		):
			self.ensure_ready()

	def test_credentials_and_images_never_logged(self):
		self.row("chosen")
		secret = "local-only-secret-key"
		self.post.return_value = response(
			{
				"choices": [{"message": {"content": json.dumps({"quantity": 12, "echo": secret})}}],
				"headers": {"Authorization": secret},
				"access_token": secret,
			}
		)
		self.call(images=[IMAGE])
		log = self.logs()[0]
		payload = log.request_payload + log.response_payload + log.raw_response
		self.assertFalse(secret in payload)
		self.assertFalse(IMAGE in payload)
		self.assertNotIn("Authorization", log.request_payload)

	def test_all_image_shapes_and_embedded_secrets_redacted(self):
		secret = "local-only-access"
		body = {
			"input": [{"content": [{"type": "input_image", "image_url": IMAGE}]}],
			"messages": [{"content": [{"type": "image", "source": {"type": "base64", "data": "bG9jYWw="}}]}],
			"headers": {"Authorization": secret},
		}
		llm.log_call(
			None,
			None,
			"local",
			"Success",
			200,
			1,
			{},
			body,
			{"echo": secret, "refresh_token": secret},
			None,
			run_id=self.prefix,
			secrets=[secret],
		)
		payload = self.logs()[0].request_payload + self.logs()[0].response_payload
		self.assertFalse(secret in payload)
		self.assertFalse("bG9jYWw=" in payload)

	def test_fallback_across_providers_redacts_access_token(self):
		from frappe_tools.ai.accounts import _store_tokens
		from frappe_tools.ai.test_accounts import tokens

		self.row("first")
		codex_account = frappe.get_doc(
			dict(
				doctype="AI Provider Account",
				account_name=self.prefix + "-codex",
				provider="OpenAI Codex",
				auth_type="ChatGPT Login",
				enabled=1,
			)
		).insert()
		secret_tokens = tokens("client")
		_store_tokens(codex_account, secret_tokens)
		self.row("codex-model")
		self.settings.task_models[-1].account = codex_account.name
		self.post.side_effect = [
			response(status=401),
			response(
				events=[
					{"type": "response.output_text.delta", "delta": '{"quantity":12}'},
					{
						"type": "response.completed",
						"response": {
							"output": [],
							"echo": secret_tokens["access_token"],
							"usage": {"input_tokens": 20, "output_tokens": 10},
						},
					},
				]
			),
		]
		result = self.call(images=[IMAGE], schema=SCHEMA)
		self.assertEqual(result["model"], "OpenAI Codex/codex-model")
		logs = self.logs()
		self.assertEqual([log.provider for log in logs], ["OpenRouter", "OpenAI Codex"])
		self.assertEqual(logs[1].total_tokens, 30)
		self.assertFalse(secret_tokens["access_token"] in logs[1].request_payload + logs[1].response_payload)
		self.assertNotIn("Authorization", logs[1].request_payload)

import base64
import json
import time
from contextlib import nullcontext
from unittest.mock import patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from frappe_tools.ai import accounts
from frappe_tools.ai.providers import ProviderError
from frappe_tools.ai.test_providers import response
from frappe_tools.patches import create_openrouter_account


def tokens(marker: str = "local", *, account_id: str = "local-account") -> dict:
	payload = {
		"exp": int(time.time()) + 3600,
		"https://api.openai.com/auth": {"chatgpt_account_id": account_id},
		"marker": marker,
	}
	encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
	return dict(
		access_token="local." + encoded + ".signature",
		refresh_token="local-refresh-" + marker,
		expires_in=3600,
	)


class TestAccounts(FrappeTestCase):
	def setUp(self):
		self._outcome.result.tb_locals = False
		frappe.set_user("Administrator")
		self.name = "LOCAL-AI-AUTH-" + frappe.generate_hash(length=10)
		self.account = frappe.get_doc(
			dict(
				doctype=accounts.DOCTYPE,
				account_name=self.name,
				provider="OpenAI Codex",
				auth_type="ChatGPT Login",
				enabled=1,
			)
		).insert()
		self.post = self.enterContext(patch("requests.post"))
		self.enterContext(patch.object(accounts, "_credential_transaction", side_effect=nullcontext))

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def expire(self):
		accounts._store_tokens(self.account, tokens("old"))
		self.account.token_expires = add_to_date(now_datetime(), minutes=2)
		self.account.save()

	def test_auth_json_import_and_encrypted_storage(self):
		value = tokens()
		result = accounts.import_auth_json(self.name, json.dumps({"tokens": value}))
		self.assertTrue(result["connected"])
		self.account.reload()
		self.assertEqual(self.account.chatgpt_account_id, "local-account")
		self.assertTrue(self.account.get_password("access_token") == value["access_token"])
		stored = frappe.db.get_value(
			"__Auth",
			{"doctype": accounts.DOCTYPE, "name": self.name, "fieldname": "access_token"},
			"password",
			order_by=None,
		)
		self.assertFalse(stored == value["access_token"])
		self.post.assert_not_called()

	def test_auth_json_explicit_account_and_invalid_input(self):
		value = tokens()
		value["account_id"] = "explicit-account"
		accounts.import_auth_json(self.name, json.dumps({"tokens": value}))
		self.assertEqual(self.account.reload().chatgpt_account_id, "explicit-account")
		for bad in ("[]", "{}", "invalid", '{"tokens":{}}'):
			with self.assertRaises(frappe.ValidationError):
				accounts.import_auth_json(self.name, bad)

	def test_import_requires_system_manager(self):
		with (
			patch.object(frappe, "only_for", side_effect=frappe.PermissionError),
			self.assertRaises(frappe.PermissionError),
		):
			accounts.import_auth_json(self.name, "{}")
		self.post.assert_not_called()

	def test_refresh_and_reuse_unexpired(self):
		self.expire()
		new = tokens("new")
		self.post.return_value = response(new)
		result = accounts.credentials(self.account, timeout=7)
		self.assertTrue(result["access_token"] == new["access_token"])
		kwargs = self.post.call_args.kwargs
		self.assertEqual(kwargs["json"]["grant_type"], "refresh_token")
		self.assertEqual(kwargs["json"]["client_id"], accounts.CLIENT_ID)
		self.assertEqual(kwargs["timeout"], 7)
		self.post.reset_mock()
		accounts.credentials(self.account.reload())
		self.post.assert_not_called()

	def test_refresh_reuses_token_stored_by_another_worker(self):
		self.expire()
		stale = frappe.get_doc(accounts.DOCTYPE, self.name)
		winner = tokens("winner")
		accounts._store_tokens(frappe.get_doc(accounts.DOCTYPE, self.name), winner)
		result = accounts.credentials(stale)
		self.post.assert_not_called()
		self.assertTrue(result["access_token"] == winner["access_token"])

	def test_refresh_failure_records_safe_error(self):
		self.expire()
		self.post.side_effect = requests.Timeout("local-secret-should-not-appear")
		with self.assertRaises(ProviderError):
			accounts.credentials(self.account)
		self.assertTrue(self.account.reload().last_error)
		self.assertFalse("local-secret-should-not-appear" in self.account.last_error)

	def test_device_start_queues_and_returns_no_tokens(self):
		self.post.return_value = response(
			dict(device_auth_id="local-device", user_code="LOCAL-CODE", interval="5")
		)
		with patch.object(frappe, "enqueue") as enqueue:
			result = accounts.start_device_login(self.name)
		self.assertEqual(set(result), {"verification_url", "user_code"})
		self.assertTrue(self.post.call_args.args[0].endswith("/api/accounts/deviceauth/usercode"))
		self.assertEqual(enqueue.call_args.kwargs["queue"], "long")
		self.assertEqual(enqueue.call_args.kwargs["interval"], 5)
		self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])

	def test_device_flow_stores_tokens_and_notifies_starting_user(self):
		new = tokens("device")
		self.post.side_effect = [
			response(status=403),
			response({"authorization_code": "local-code", "code_verifier": "local-verifier"}),
			response(new),
		]
		with (
			patch.object(accounts.time, "sleep") as sleep,
			patch.object(frappe, "publish_realtime") as publish,
		):
			accounts.complete_device_login(self.name, "Administrator", "local-device", "LOCAL-CODE", 5)
		self.assertTrue(self.account.reload().get_password("access_token") == new["access_token"])
		sleep.assert_called_once()
		kwargs = self.post.call_args.kwargs
		self.assertEqual(kwargs["data"]["grant_type"], "authorization_code")
		self.assertTrue(kwargs["data"]["redirect_uri"].endswith("/deviceauth/callback"))
		self.assertEqual(publish.call_args.kwargs["user"], "Administrator")
		self.assertTrue(publish.call_args.args[1]["success"])

	def test_device_failure_notifies_without_tokens(self):
		self.post.return_value = response(status=400)
		with patch.object(frappe, "publish_realtime") as publish:
			accounts.complete_device_login(self.name, "Administrator", "local-device", "LOCAL-CODE", 5)
		self.assertFalse(publish.call_args.args[1]["success"])
		self.assertIn("HTTP 400", self.account.reload().last_error)

	def test_provider_auth_type_combinations(self):
		allowed = {
			"OpenRouter": ["API Key"],
			"OpenAI Codex": ["ChatGPT Login"],
			"Anthropic": ["API Key", "Setup Token"],
		}
		for provider, valid in allowed.items():
			for auth in ("API Key", "ChatGPT Login", "Setup Token"):
				self.account.provider, self.account.auth_type = provider, auth
				if auth in valid:
					self.account.validate()
				else:
					with self.assertRaises(frappe.ValidationError):
						self.account.validate()

	def test_patch_idempotence_does_not_replace_account(self):
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(create_openrouter_account, "get_decrypted_password") as password,
		):
			create_openrouter_account.execute()
			password.assert_not_called()
		with (
			patch.object(frappe.db, "exists", side_effect=[False, True]),
			patch.object(create_openrouter_account, "get_decrypted_password", return_value="local-only-patch-key"),
			patch.object(frappe, "get_doc") as doc,
		):
			create_openrouter_account.execute()
			create_openrouter_account.execute()
			self.assertEqual(doc.call_count, 1)
			self.assertEqual(doc.call_args.args[0]["provider"], "OpenRouter")

	def test_global_read_role_cannot_read_provider_accounts(self):
		from frappe_tools.frappe_tools.doctype.ai_provider_account.ai_provider_account import (
			has_permission,
			permission_query_conditions,
		)

		with patch.object(frappe, "get_roles", return_value=["AI Bot"]):
			self.assertFalse(has_permission(self.account, "read", "local-user"))
			self.assertEqual(permission_query_conditions("local-user"), "1=0")
		with patch.object(frappe, "get_roles", return_value=["System Manager"]):
			self.assertTrue(has_permission(self.account, "read", "local-user"))
			self.assertEqual(permission_query_conditions("local-user"), "")

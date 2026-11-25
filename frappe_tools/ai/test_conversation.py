import hashlib
import hmac
import json
from unittest.mock import Mock, patch

import frappe
import requests
from frappe.tests import UnitTestCase

from frappe_tools.ai import conversation

SECRET = "local-agent-secret-local-agent-secret"


def account(**values) -> frappe._dict:
	return frappe._dict({"provider": "Genie", "auth_type": "Shared Secret", "api_key": SECRET,
		"base_url": "http://127.0.0.1:8796/", **values})


class TestConversation(UnitTestCase):
	"""A whole conversation handed to an agent model: who is an agent, the signed post and its failures."""

	def route(self, found=None):
		routes = [] if found is None else [(found, frappe._dict(model_id="agent"))]
		return patch.object(conversation.client, "_usable_route", side_effect=lambda name: iter(routes))

	def hand_over(self, outcome, found=None, **kwargs):
		key = "side_effect" if isinstance(outcome, Exception) else "return_value"
		post = patch.object(conversation.requests, "post", **{key: outcome})
		with self.route(found or account()), post as sent:
			conversation.hand_over("Agent Model", "relay", {"text": "வணக்கம்", "version": 1}, **kwargs)
		return sent

	def test_only_an_enabled_agent_provider_is_a_conversation_agent(self):
		with self.route(account()):
			self.assertTrue(conversation.is_conversation_agent("Agent Model"))
		with self.route(account(provider="OpenRouter")):
			self.assertFalse(conversation.is_conversation_agent("Agent Model"))
		with self.route():  # A disabled model or account has no usable route.
			self.assertFalse(conversation.is_conversation_agent("Agent Model"))

	def test_hand_over_signs_the_raw_compact_body(self):
		sent = self.hand_over(Mock(status_code=202), headers={"X-Relay-Delivery": "MSG-1", "X-Hub-Signature-256": "forged"})
		url, kwargs = sent.call_args.args[0], sent.call_args.kwargs
		self.assertEqual(url, "http://127.0.0.1:8796/relay")
		self.assertEqual(kwargs["data"], '{"text":"வணக்கம்","version":1}'.encode())
		expected = hmac.new(SECRET.encode(), kwargs["data"], hashlib.sha256).hexdigest()
		self.assertEqual(kwargs["headers"], {"X-Relay-Delivery": "MSG-1", "Content-Type": "application/json",
			"X-Hub-Signature-256": "sha256=" + expected})
		self.assertEqual((kwargs["timeout"], kwargs["allow_redirects"]), (10, False))
		self.assertEqual(json.loads(kwargs["data"]), {"text": "வணக்கம்", "version": 1})

	def test_failures_raise_a_short_reason(self):
		for outcome, reason in ((Mock(status_code=503), "HTTP 503"), (Mock(status_code=302), "HTTP 302"),
				(requests.Timeout("slow"), "Timeout"), (requests.ConnectionError("down"), "ConnectionError")):
			with self.subTest(reason=reason), self.assertRaises(conversation.HandOverError) as raised:
				self.hand_over(outcome)
			self.assertEqual(str(raised.exception), reason)

	def test_a_model_that_is_not_an_agent_or_a_bad_url_is_not_posted(self):
		for found in (None, account(provider="OpenRouter"), account(base_url="http://agent.example.test")):
			with self.subTest(found=found), self.route(found), patch.object(conversation.requests, "post") as sent:
				with self.assertRaises(conversation.HandOverError):
					conversation.hand_over("Agent Model", "relay", {})
				sent.assert_not_called()

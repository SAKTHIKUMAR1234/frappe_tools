from unittest.mock import Mock, patch

import requests
from frappe.tests import UnitTestCase

from frappe_tools.scan_categorization import jev


class TestJev(UnitTestCase):
	def setUp(self):
		self.questions = {"layout": {"type": "choice", "instructions": "Pick", "criteria": {"Bill": "Invoice"}}}
		self.answer = {"type": "choice", "choice": "Bill", "confidence": 0.91}
		self.settings = patch.object(jev.llm, "ensure_ready", return_value=Mock(get_api_key=lambda: "test-key"))
		self.settings.start()
		self.addCleanup(self.settings.stop)
		self.logger = patch.object(jev.llm, "_log_call")
		self.log = self.logger.start()
		self.addCleanup(self.logger.stop)

	def test_request_and_answer(self):
		response = Mock(status_code=200)
		response.json.return_value = {"answers": {"layout": self.answer}, "usage": {"input_tokens": 42, "cost": 0.001}}
		with patch.object(jev.requests, "post", return_value=response) as post:
			self.assertEqual(jev.decide({"bill_no": "LOCAL-42"}, self.questions, "Purchase Invoice"), {"layout": self.answer})
		body = post.call_args.kwargs["json"]
		self.assertEqual(body, {"model": jev.MODEL, "state": {"bill_no": "LOCAL-42"}, "questions": self.questions})
		self.assertEqual(post.call_args.args, (jev.URL,))
		self.assertEqual(post.call_args.kwargs["timeout"], 60)
		self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")
		self.assertEqual(self.log.call_args.args[6]["prompt_tokens"], 42)
		self.assertEqual(self.log.call_args.args[6]["cost"], 0.001)

	def test_invalid_answers_fail_closed(self):
		for answer in ({}, {**self.answer, "choice": "Other"}, {**self.answer, "confidence": -1},
			{**self.answer, "confidence": float("nan")}, {**self.answer, "confidence": True},
			{**self.answer, "confidence": "0.9"}, {**self.answer, "type": "score"}):
			with self.subTest(answer=answer):
				response = Mock(status_code=200)
				response.json.return_value = {"answers": {"layout": answer}}
				with patch.object(jev.requests, "post", return_value=response), self.assertRaises(ValueError):
					jev.decide({}, self.questions, "Purchase Invoice")
				self.assertEqual(self.log.call_args.args[3], "Error")

	def test_timeout_is_logged_and_raised(self):
		with patch.object(jev.requests, "post", side_effect=requests.Timeout("slow")), self.assertRaises(requests.Timeout):
			jev.decide({}, self.questions, "Purchase Invoice")
		self.assertEqual(self.log.call_args.args[3], "Timeout")

	def test_http_error_is_logged(self):
		response = Mock(status_code=503)
		response.raise_for_status.side_effect = requests.HTTPError("Unavailable")
		with patch.object(jev.requests, "post", return_value=response), self.assertRaises(requests.HTTPError):
			jev.decide({}, self.questions, "Purchase Invoice")
		self.assertEqual(self.log.call_args.args[4], 503)

	def test_oversized_state_never_calls_http(self):
		with patch.object(jev.requests, "post") as post, self.assertRaises(ValueError):
			jev.decide({"text": "x" * jev.MAX_REQUEST_BYTES}, self.questions, "Purchase Invoice")
		post.assert_not_called()

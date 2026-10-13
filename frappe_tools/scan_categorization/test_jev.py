from copy import deepcopy
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
		self.logger = patch.object(jev.llm, "log_call")
		self.log = self.logger.start()
		self.addCleanup(self.logger.stop)

	def test_request_and_answer(self):
		response = Mock(status_code=200)
		response.json.return_value = {"answers": {"layout": self.answer}, "usage": {"input_tokens": 42, "cost": 0.001}}
		context = {"phase": "Layout Decision", "reference_doctype": "Scanned Document",
			"reference_name": "LOCAL-SCAN", "run_id": "local-run-id"}
		with patch.object(jev.requests, "post", return_value=response) as post:
			self.assertEqual(jev.decide({"bill_no": "LOCAL-42"}, self.questions, "Purchase Invoice", **context), {"layout": self.answer})
		self.assertEqual(self.log.call_args.kwargs, context)
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
		with patch.object(jev.requests, "post") as post, self.assertRaisesRegex(ValueError, "26,000-token budget after shrinking"):
			jev.decide({"text": "x" * int(jev.MAX_INPUT_TOKENS * 3.5)}, self.questions, "Purchase Invoice")
		post.assert_not_called()

	def test_request_over_24000_bytes_within_token_budget_is_sent(self):
		state = {"text": "Printed invoice data " * 3000}
		self.assertLess(jev._estimated_tokens(state, self.questions), jev.MAX_INPUT_TOKENS)
		response = Mock(status_code=200)
		response.json.return_value = {"answers": {"layout": self.answer}}
		with patch.object(jev.requests, "post", return_value=response) as post:
			jev.decide(state, self.questions, "Purchase Invoice")
		self.assertEqual(post.call_args.kwargs["json"]["state"], state)

	def test_small_state_preserves_all_summary_page_data(self):
		from frappe_tools.scan_categorization.test_categorize import page_data

		page = {key: value for key, value in page_data(1).items() if key not in jev.RAW_FIELDS}
		state = {"document": {"bill_no": "LOCAL-42"}, "pages": [page]}
		self.assertEqual(jev._bounded_state(state, self.questions), state)

	def test_size_guard_order_and_preserved_fields(self):
		from frappe_tools.scan_categorization.test_categorize import page_data

		for stage in ("items", "other_fields", "addresses"):
			with self.subTest(stage=stage):
				page = page_data(1)
				page["items"] = [{"description": "Printed row " * 250}] * 40
				if stage in ("other_fields", "addresses"):
					page["other_fields"] = {"Terms": "t" * 100000}
				if stage == "addresses":
					page["parties"]["supplier"]["address"] = "a" * 100000
				state = {"document": {"bill_no": "LOCAL-42"}, "pages": [page]}
				original = deepcopy(state)
				self.assertGreater(jev._estimated_tokens(state, self.questions), jev.MAX_INPUT_TOKENS)
				bounded = jev._bounded_state(state, self.questions)
				self.assertLessEqual(jev._estimated_tokens(bounded, self.questions), jev.MAX_INPUT_TOKENS)
				self.assertEqual(state, original)
				self.assertEqual(bounded["document"], state["document"])
				result = bounded["pages"][0]
				self.assertEqual(result["items"], page["items"][:15])
				self.assertEqual(result["items_truncated"], 25)
				if stage == "items":
					self.assertEqual(result["other_fields"], page["other_fields"])
				else:
					self.assertNotIn("other_fields", result)
				expected_address = page["parties"]["supplier"]["address"]
				self.assertEqual(result["parties"]["supplier"]["address"], expected_address[:200] if stage == "addresses" else expected_address)
				for key in ("document_type", "document_number", "document_date", "references", "taxes", "totals"):
					self.assertEqual(result[key], page[key])
				for key in ("name", "gstin"):
					self.assertEqual(result["parties"]["supplier"][key], page["parties"]["supplier"][key])

	def test_guard_counts_questions_and_protects_essential_data(self):
		state = {"pages": [{"references": {"irn": "x" * 92000}}]}
		for current, questions in ((state, self.questions), ({}, {"instructions": "x" * 92000})):
			with patch.object(jev.requests, "post") as post, self.assertRaises(ValueError):
				jev.decide(current, questions, "Purchase Invoice")
			post.assert_not_called()

	def test_guard_keeps_every_page_and_only_counts_omitted_rows(self):
		from frappe_tools.scan_categorization.test_categorize import page_data

		pages = [page_data(1, items=None), page_data(2), page_data(3, items=[{"qty": "2"}] * 40)]
		pages[0]["other_fields"] = {"Terms": "x" * 92000}
		result = jev._bounded_state({"pages": pages}, self.questions)["pages"]
		self.assertEqual([page["page_no"] for page in result], [1, 2, 3])
		self.assertIsNone(result[0]["items"])
		self.assertEqual(result[1]["items"], pages[1]["items"])
		self.assertNotIn("items_truncated", result[0])
		self.assertNotIn("items_truncated", result[1])
		self.assertEqual(result[2]["items_truncated"], 25)
		self.assertEqual(len(result[2]["items"]), 15)

	def test_http_receives_bounded_state_without_trimming_saved_input(self):
		state = {"pages": [{"items": [{"description": "x" * 3000}] * 40, "other_fields": None}]}
		response = Mock(status_code=200)
		response.json.return_value = {"answers": {"layout": self.answer}}
		with patch.object(jev.requests, "post", return_value=response) as post:
			jev.decide(state, self.questions, "Purchase Invoice")
		self.assertEqual(len(post.call_args.kwargs["json"]["state"]["pages"][0]["items"]), 15)
		self.assertEqual(len(state["pages"][0]["items"]), 40)

	def test_raw_page_fields_are_always_dropped_from_state(self):
		from frappe_tools.scan_categorization.test_categorize import page_data

		pages = [page_data(1)]
		result = jev._bounded_state({"pages": pages}, self.questions)["pages"][0]
		for field in ("full_text", "handwritten", "item_table"):
			self.assertNotIn(field, result)
			self.assertIn(field, pages[0])
		self.assertEqual(result["items"], pages[0]["items"])

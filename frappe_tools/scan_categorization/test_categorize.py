import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import frappe
from frappe.tests import UnitTestCase

from frappe_tools.scan_categorization import PAGE_SCHEMA, _extract_pages, get_page_data
from frappe_tools.scan_categorization import categorize as flow
from frappe_tools.scan_categorization.test_rotation import page_image
from frappe_tools.utils import llm, save_file_always_new


def page_data(number: int, **values) -> dict:
	return {
		**deepcopy(PAGE_SCHEMA), "page_no": number, "document_type": "Tax Invoice",
		"document_number": "LOCAL-42", "document_date": "12/10/2026",
		"parties": {"supplier": {"name": "Local Scanner Supplier", "gstin": "33LOCALTEST001Z1",
			"address": "42 Local Test Street, Test City"}},
		"references": {"po_no": "LOCAL-PO-5", "vehicle_no": "TN 01 TEST 0042"},
		"items": [{"description": "Test fabric", "hsn": "5208", "qty": "5.00", "uom": "Nos",
			"weight_kgs": "12.50", "rate": "250.00", "amount": "1,250.00", "roll_no": "R-42"}],
		"taxes": {"CGST": "112.50", "SGST": "112.50"},
		"totals": {"taxable": "1,250.00", "grand_total": "1,475.00", "round_off": "0.00"},
		"other_fields": {"Payment terms": "30 days"}, **values,
	}


class TestCategorize(UnitTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.addCleanup(frappe.db.rollback)
		self.commit = patch.object(frappe.db, "commit")
		self.commit.start()
		self.addCleanup(self.commit.stop)
		self.settings = frappe._dict(enable_ai_categorization=1, ai_confidence_threshold=0.8, ai_batch_limit=200)
		self.patch = patch.object(flow, "_settings", return_value=(self.settings, 0.8))
		self.patch.start()
		self.addCleanup(self.patch.stop)
		self.ready = patch.object(llm, "ensure_ready", return_value=Mock(get_api_key=lambda: "local-test-key"))
		self.ready.start()
		self.addCleanup(self.ready.stop)
		self.vision = patch.object(llm, "call_vision", return_value={"data": {"pages": [
			page_data(1, rotation=90),
			page_data(2, document_type="Delivery Challan"),
		]}})
		self.vision_mock = self.vision.start()
		self.addCleanup(self.vision.stop)
		self.http = patch("requests.post", side_effect=self.response)
		self.post = self.http.start()
		self.addCleanup(self.http.stop)
		self.confidence, self.layout_confidence = 0.9, 0.95
		self.prefix = "_Test Scan Local " + frappe.generate_hash(length=8)
		# Insert a local-only source fixture without ERPNext's fiscal-year test loader.
		self.bill = frappe.get_doc({
			"doctype": "Purchase Invoice", "name": self.prefix, "supplier_name": "Local Scanner Supplier",
			"bill_no": "LOCAL-42", "currency": "INR", "grand_total": 1250, "total_qty": 5,
		})
		self.bill.db_insert()
		frappe.get_doc({
			"doctype": "Purchase Invoice Item", "parent": self.bill.name, "parenttype": "Purchase Invoice",
			"parentfield": "items", "idx": 1, "item_code": "LOCAL-SCAN-ITEM", "item_name": "Test fabric",
			"qty": 5, "rate": 250, "amount": 1250, "uom": "Nos",
		}).db_insert()
		self.titles = []
		for suffix in ("Invoice", "Receipt"):
			self.titles.append(frappe.get_doc({"doctype": "Document Layout Section Title", "title": self.prefix + suffix}).insert().name)
		self.layout = frappe.get_doc({
			"doctype": "Document Scanner Layout", "layout_name": self.prefix, "layout_doctype": "Purchase Invoice",
			"layout_doctype_sections": [
				{"title": self.titles[0], "layout_type": "Single Page"},
				{"title": self.titles[1], "layout_type": "Series Vertical"},
			],
		}).insert()
		self.scan = self.new_scan(status="Queued")
		self.details = self.add_pages(self.scan)

	def new_scan(self, status="", creation=None, reviewed=0):
		scan = frappe.get_doc({
			"doctype": flow.SCAN, "_doctype": "Purchase Invoice", "_docname": self.bill.name,
			"scanner_layout": self.layout.name, "ai_check_status": status, "ai_reviewed": reviewed,
		}).insert()
		if creation:
			frappe.db.set_value(flow.SCAN, scan.name, "creation", creation)
		return scan

	def add_pages(self, scan):
		image = page_image()
		details = []
		for number in (1, 2):
			page = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE) if number == 1 else image
			content = cv2.imencode(".png", page)[1].tobytes()
			file = save_file_always_new(f"{self.prefix}-{scan.name}-{number}.png", content, flow.SCAN, scan.name, is_private=1)
			details.append(frappe.get_doc({
				"doctype": flow.DETAIL, "scanner_document": scan.name, "page_no": number,
				"attachment": file.file_url, "layout_type": "Single Page", "page_type": "Front", "title": self.titles[0],
			}).insert())
		return details

	def response(self, _url, **kwargs):
		answers = {}
		for key in kwargs["json"]["questions"]:
			choice = self.layout.name if key == "layout" else self.titles[int(key.split("_")[1]) - 1]
			answers[key] = {"type": "choice", "choice": choice, "confidence": self.layout_confidence if key == "layout" else self.confidence}
		response = Mock(status_code=200)
		response.json.return_value = {"answers": answers, "usage": {"input_tokens": 100, "cost": 0.002}}
		return response

	def test_applies_layout_sections_and_keeps_original(self):
		original = self.details[0].attachment
		other = frappe.copy_doc(self.layout)
		other.layout_name = self.prefix + " alternate"
		other.insert()
		frappe.db.set_value(flow.SCAN, self.scan.name, "scanner_layout", other.name)
		flow.categorize_scan(self.scan.name)
		self.scan.reload()
		self.assertEqual(self.scan.ai_check_status, "Done", self.scan.ai_error)
		self.assertEqual(self.scan.ai_layout_confidence, 0.9)
		self.assertEqual(self.scan.scanner_layout, self.layout.name)
		self.details[0].reload()
		self.details[1].reload()
		self.assertEqual(self.details[0].original_attachment, original)
		self.assertNotEqual(self.details[0].attachment, original)
		self.assertTrue(self.details[0].attachment.endswith(".png"))
		self.assertTrue(frappe.db.exists("File", {"file_url": original}))
		self.assertTrue(frappe.db.exists("File", {"file_url": self.details[0].attachment, "attached_to_name": self.scan.name, "is_private": 1}))
		self.assertEqual(self.details[1].title, self.titles[1])
		self.assertEqual(self.details[1].layout_type, "Series Vertical")
		self.assertFalse(self.details[1].original_attachment)
		self.vision_mock.assert_called_once()
		self.assertEqual(len(self.vision_mock.call_args.args[0]), 2)
		self.assertEqual(self.post.call_count, 2)
		state = self.post.call_args.kwargs["json"]["state"]
		for call in self.post.call_args_list:
			self.assertEqual(call.kwargs["json"]["state"]["pages"], self.vision_mock.return_value["data"]["pages"])
		self.assertEqual(float(state["document"]["grand_total"]), 1250)
		self.assertEqual(float(state["document"]["items"][0]["amount"]), 1250)
		self.assertEqual(set(self.post.call_args.kwargs["json"]["questions"]), {"page_1", "page_2"})
		logs = frappe.get_all("Document AI Call Log", filters={"model": "typesafe/jev-1.13", "request_payload": ["like", "%" + self.prefix + "%"]}, fields=["provider", "prompt_tokens", "cost_usd"])
		self.assertEqual(len(logs), 2)
		self.assertTrue(all(log.provider == "OpenRouter" and log.prompt_tokens == 100 and log.cost_usd == 0.002 for log in logs))

	def test_low_page_confidence_changes_no_content(self):
		self.confidence = 0.79
		before = [d.reload().as_dict() for d in self.details]
		with patch.object(flow, "correct_image") as correction:
			flow.categorize_scan(self.scan.name)
		correction.assert_not_called()
		self.scan.reload()
		self.assertEqual(self.scan.ai_check_status, "Needs Review")
		for original, detail in zip(before, self.details, strict=True):
			detail.reload()
			for key in ("attachment", "original_attachment", "title", "layout_type", "modified"):
				self.assertEqual(detail.get(key), original[key])
			self.assertTrue(detail.ai_page_data)
			self.assertTrue(detail.ai_page_data_on)

	def test_external_calls_and_correction_precede_scan_locks_and_writes(self):
		original_vision = self.vision_mock.return_value
		original_correction = flow.correct_image
		with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
			def assert_unlocked():
				for call in sql.call_args_list:
					query = str(call.args[0]).lower()
					if "tabscanned document" in query:
						self.assertNotIn("for update", query)
						self.assertFalse(query.lstrip().startswith(("update", "insert", "delete")), query)

			def vision(*args, **kwargs):
				assert_unlocked()
				return original_vision

			def response(*args, **kwargs):
				assert_unlocked()
				return self.response(*args, **kwargs)

			def correct(*args):
				assert_unlocked()
				return original_correction(*args)

			self.vision_mock.side_effect = vision
			self.post.side_effect = response
			with patch.object(flow, "correct_image", side_effect=correct):
				flow.categorize_scan(self.scan.name)
			queries = [str(call.args[0]).lower() for call in sql.call_args_list]
			for table in ("tabscanned document`", "tabscanned document detail`"):
				self.assertTrue(any(table in query and "for update" in query for query in queries))
		self.assertEqual(self.scan.reload().ai_check_status, "Done", self.scan.ai_error)

	def test_changes_during_ai_require_review_without_applying(self):
		def add_page():
			frappe.copy_doc(self.details[0]).insert()

		changes = {
			"attachment": lambda: frappe.db.set_value(flow.DETAIL, self.details[0].name, "attachment", self.details[1].attachment),
			"deleted": lambda: frappe.db.set_value(flow.DETAIL, self.details[0].name, "is_deleted", 1),
			"removed": lambda: frappe.db.delete(flow.DETAIL, self.details[0].name),
			"reordered": lambda: frappe.db.set_value(flow.DETAIL, self.details[0].name, "page_no", 3),
			"moved": lambda: frappe.db.set_value(flow.DETAIL, self.details[0].name, "scanner_document", "local-other-scan"),
			"added": add_page,
			"status": lambda: frappe.db.set_value(flow.SCAN, self.scan.name, "ai_check_status", "Done"),
			"reviewed": lambda: flow.mark_reviewed(self.scan.name),
		}
		for label, change in changes.items():
			with self.subTest(change=label):
				frappe.db.savepoint("concurrent_change")
				def response(*args, **kwargs):
					if "page_1" in kwargs["json"]["questions"]:
						change()
					return self.response(*args, **kwargs)
				self.post.side_effect = response
				with patch.object(flow, "_apply") as apply:
					flow.categorize_scan(self.scan.name)
				apply.assert_not_called()
				self.scan.reload()
				if label == "reviewed":
					self.assertEqual(self.scan.ai_check_status, "Done")
				else:
					self.assertEqual(self.scan.ai_check_status, "Needs Review")
					self.assertIn("changed during AI processing", self.scan.ai_error)
				self.assertEqual(self.scan.ai_reviewed, int(label == "reviewed"))
				self.assertFalse(self.details[1].reload().ai_page_data)
				self.assertEqual(self.details[1].title, self.titles[0])
				frappe.db.rollback(save_point="concurrent_change")

	def test_change_during_image_correction_is_rechecked(self):
		correct = flow.correct_image
		def change(*args):
			frappe.db.set_value(flow.DETAIL, self.details[0].name, "is_deleted", 1)
			return correct(*args)
		with patch.object(flow, "correct_image", side_effect=change):
			flow.categorize_scan(self.scan.name)
		self.assertEqual(self.scan.reload().ai_check_status, "Needs Review")
		self.assertFalse(self.details[0].reload().original_attachment)

	def test_missing_or_out_of_order_vision_page_fails_scan(self):
		for pages in ([page_data(1)], [page_data(2), page_data(1)], [page_data(1), page_data(1)]):
			with self.subTest(pages=pages):
				frappe.db.set_value(flow.SCAN, self.scan.name, "ai_check_status", "Queued")
				self.vision_mock.return_value = {"data": {"pages": pages}}
				with patch.object(frappe, "log_error"):
					flow.categorize_scan(self.scan.name)
				self.assertEqual(self.scan.reload().ai_check_status, "Failed")
				self.assertIn("every page exactly once", self.scan.ai_error)
				self.assertFalse(self.details[0].reload().ai_page_data)
		self.post.assert_not_called()

	def test_page_questions_follow_active_detail_order(self):
		frappe.db.set_value(flow.DETAIL, self.details[0].name, "page_no", 5)
		frappe.db.set_value(flow.DETAIL, self.details[1].name, "page_no", 9)
		flow.categorize_scan(self.scan.name)
		self.assertEqual(self.scan.reload().ai_check_status, "Done", self.scan.ai_error)
		for detail, title in zip(self.details, self.titles, strict=True):
			self.assertEqual(detail.reload().title, title)
		self.assertEqual(list(self.post.call_args.kwargs["json"]["questions"]), ["page_1", "page_2"])

	def test_corrected_jpeg_attachment_and_cache_keep_format(self):
		content = cv2.imencode(".jpg", cv2.rotate(page_image(), cv2.ROTATE_90_COUNTERCLOCKWISE))[1].tobytes()
		original = save_file_always_new(f"{self.prefix}.jpg", content, flow.SCAN, self.scan.name, is_private=1)
		frappe.db.set_value(flow.DETAIL, self.details[0].name, "attachment", original.file_url)
		flow.categorize_scan(self.scan.name)
		self.assertEqual(self.scan.reload().ai_check_status, "Done", self.scan.ai_error)
		detail = self.details[0].reload()
		self.assertEqual(detail.original_attachment, original.file_url)
		self.assertTrue(detail.attachment.endswith(".jpg"))
		file = frappe.get_doc("File", {"file_url": detail.attachment})
		self.assertTrue(file.get_content().startswith(b"\xff\xd8\xff"))
		self.assertEqual(get_page_data(self.scan.name)[0]["rotation"], 0)
		self.vision_mock.assert_called_once()

	def test_low_layout_confidence_and_exact_threshold(self):
		self.layout_confidence = 0.7
		flow.categorize_scan(self.scan.name)
		self.assertEqual(self.scan.reload().ai_check_status, "Needs Review")
		frappe.db.set_value(flow.SCAN, self.scan.name, "ai_check_status", "Queued")
		self.layout_confidence = self.confidence = 0.8
		flow.categorize_scan(self.scan.name)
		self.assertEqual(self.scan.reload().ai_check_status, "Done")

	def test_failure_records_error_and_does_not_escape(self):
		self.post.side_effect = RuntimeError("Local simulated API failure")
		with patch.object(frappe, "log_error") as log:
			flow.categorize_scan(self.scan.name)
		self.assertEqual(self.scan.reload().ai_check_status, "Failed")
		self.assertIn("simulated API failure", self.scan.ai_error)
		log.assert_called_once()
		self.assertTrue(frappe.db.exists("Document AI Call Log", {"model": "typesafe/jev-1.13", "status": "Error"}))

	def test_partial_application_is_rolled_back(self):
		original = self.details[0].attachment
		self.vision_mock.return_value["data"]["pages"][0]["rotation"] = 180
		files = []
		def save(*args, **kwargs):
			file = save_file_always_new(*args, **kwargs)
			files.append(file)
			return file

		set_value = frappe.db.set_value
		def fail_second(doctype, name, *args, **kwargs):
			if doctype == flow.DETAIL and name == self.details[1].name and "title" in args[0]:
				raise RuntimeError("Second page write failed")
			return set_value(doctype, name, *args, **kwargs)
		with patch.object(frappe.db, "set_value", side_effect=fail_second), patch.object(frappe, "log_error"), patch.object(flow, "save_file_always_new", side_effect=save):
			flow.categorize_scan(self.scan.name)
		self.assertEqual(self.scan.reload().ai_check_status, "Failed")
		self.assertEqual(self.details[0].reload().attachment, original)
		self.assertFalse(self.details[0].original_attachment)
		self.assertEqual(len(files), 1)
		self.assertFalse(frappe.db.exists("File", files[0].name))
		self.assertFalse(Path(files[0].get_full_path()).exists())
		self.assertEqual(get_page_data(self.scan.name), self.vision_mock.return_value["data"]["pages"])
		self.vision_mock.assert_called_once()

	def test_review_and_completed_jobs_are_never_overwritten(self):
		flow.mark_reviewed(self.scan.name)
		flow.categorize_scan(self.scan.name)
		self.assertFalse(flow.run_ai_check(self.scan.name)["queued"])
		self.vision_mock.assert_not_called()
		self.assertEqual(self.scan.reload().ai_reviewed, 1)
		for status in ("Done", "Needs Review", "Failed", ""):
			frappe.db.set_value(flow.SCAN, self.scan.name, {"ai_reviewed": 0, "ai_check_status": status})
			flow.categorize_scan(self.scan.name)
		self.vision_mock.assert_not_called()

	def test_nightly_date_limit_deleted_reviewed_and_dedup(self):
		self.settings.ai_process_since = "2099-01-01"
		self.settings.ai_batch_limit = 1
		old = self.new_scan(creation="2098-12-31")
		self.add_pages(old)
		first = self.new_scan(creation="2099-01-01")
		self.add_pages(first)
		second = self.new_scan(creation="2099-01-02")
		self.add_pages(second)
		deleted = self.new_scan(creation="2099-01-01")
		for detail in self.add_pages(deleted):
			frappe.db.set_value(flow.DETAIL, detail.name, "is_deleted", 1)
		reviewed = self.new_scan(creation="2099-01-01", reviewed=1)
		self.add_pages(reviewed)
		self.new_scan(creation="2099-01-01")
		for status in ("Done", "Needs Review", "Failed"):
			self.add_pages(self.new_scan(creation="2099-01-01", status=status))
		with patch.object(frappe, "enqueue") as enqueue:
			self.assertEqual(flow.enqueue_nightly(), 1)
			self.assertEqual(enqueue.call_args.kwargs["name"], first.name)
			self.assertEqual(enqueue.call_args.kwargs["queue"], "long")
			self.assertEqual(enqueue.call_args.kwargs["job_id"], f"scan-categorize::{first.name}")
			self.assertTrue(enqueue.call_args.kwargs["deduplicate"])
			self.assertFalse(flow.run_ai_check(first.name)["queued"])
			self.assertEqual(enqueue.call_count, 1)
			self.assertEqual(flow.enqueue_nightly(), 1)
			self.assertEqual(enqueue.call_args.kwargs["name"], second.name)
			self.assertEqual(flow.enqueue_nightly(), 0)

	def test_disabled_nightly(self):
		self.settings.enable_ai_categorization = 0
		with patch.object(frappe, "enqueue") as enqueue:
			self.assertEqual(flow.enqueue_nightly(), 0)
			with self.assertRaises(frappe.ValidationError):
				flow.run_ai_check(self.scan.name)
		enqueue.assert_not_called()

	def test_permission_denied(self):
		with patch.object(frappe, "get_roles", return_value=["Scanner User"]), patch.object(frappe, "has_permission", return_value=False):
			with self.assertRaises(frappe.PermissionError):
				flow.run_ai_check(self.scan.name)
			with self.assertRaises(frappe.PermissionError):
				flow.mark_reviewed(self.scan.name)

	def test_linked_write_permission_allows_queue(self):
		frappe.db.set_value(flow.SCAN, self.scan.name, "ai_check_status", "Failed")
		with patch.object(frappe, "get_roles", return_value=["Scanner User"]), patch.object(frappe, "has_permission", return_value=True) as permission, patch.object(frappe, "enqueue"):
			self.assertTrue(flow.run_ai_check(self.scan.name)["queued"])
		permission.assert_called_with("Purchase Invoice", ptype="write", doc=self.bill.name)

	def test_document_state_is_bounded(self):
		item = frappe._dict({key: "x" * 10000 for key in flow.ITEM_FIELDS})
		doc = frappe._dict(items=[item] * 2000, **{field: "x" * 10000 for field in flow.MAIN_FIELDS})
		with patch.object(frappe, "get_doc", return_value=doc):
			state = flow._document_state(self.scan)
		self.assertLess(len(json.dumps(state).encode()), 8500)
		self.assertGreater(state["items_omitted"], 0)

	def test_vision_rejects_missing_duplicate_or_invalid_pages(self):
		invalid = [[], [page_data(1)] * 2, [page_data(1, rotation=45)],
			[page_data(True)], [page_data(0)], [page_data(1, rotation=True)],
			[page_data(1, is_blank_or_unreadable="false")], [page_data(1, document_number=42)],
			[page_data(1, items=[{"qty": 5}])], [page_data(1, items="row")],
			[page_data(1, parties=[])], [page_data(1, taxes="tax")],
			[{"page_no": 1, "rotation": 0, "summary": "Incomplete"}]]
		for pages in invalid:
			with self.subTest(pages=pages):
				self.vision_mock.return_value = {"data": {"pages": pages}}
				with self.assertRaises(ValueError):
					_extract_pages(["image"], target_doctype="Purchase Invoice")

	def test_vision_prompt_and_full_response(self):
		full = page_data(1, items=[page_data(1)["items"][0]] * 30,
			other_fields={"Terms": "Printed terms " * 100})
		blank = {key: None for key in PAGE_SCHEMA}
		blank.update(page_no=2, rotation=0, is_blank_or_unreadable=True)
		self.vision_mock.return_value = {"data": {"pages": [full, blank]}}
		self.assertEqual(_extract_pages(["one", "two"], target_doctype="Purchase Invoice"), [full, blank])
		args = self.vision_mock.call_args
		self.assertEqual(args.args[0], ["one", "two"])
		self.assertEqual(args.kwargs["target_doctype"], "Purchase Invoice")
		self.assertIn("WHOLE data", args.args[1])
		self.assertIn("as printed text", args.args[2])
		for field in PAGE_SCHEMA:
			self.assertIn(field, args.args[2])

	def test_invalid_vision_response_saves_no_partial_pages(self):
		for data in (None, [], {}, {"pages": [page_data(1), page_data(1)]},
			{"pages": [page_data(1), page_data(3)]},
			{"pages": [page_data(1), page_data(2, items=["invalid row"])]}):
			with self.subTest(data=data):
				self.vision_mock.return_value = {"data": data}
				with self.assertRaises(ValueError):
					get_page_data(self.scan.name)
				for detail in self.details:
					self.assertFalse(detail.reload().ai_page_data)
					self.assertFalse(detail.ai_page_data_on)

	def test_page_data_is_saved_and_reused(self):
		pages = get_page_data(self.scan.name)
		for page, detail in zip(pages, self.details, strict=True):
			detail.reload()
			saved = frappe.parse_json(detail.ai_page_data)
			self.assertEqual(saved.pop("_source")["attachment"], detail.attachment)
			self.assertEqual(saved, page)
			self.assertTrue(detail.ai_page_data_on)
		self.assertEqual(get_page_data(self.scan.name), pages)
		self.vision_mock.assert_called_once()
		self.post.assert_not_called()

	def test_vision_uses_json_mode_and_logs_full_response(self):
		self.vision.stop()
		settings = frappe._dict(model="local/test-vision", max_attempts=1, max_tokens=16000,
			request_timeout=30, get_api_key=lambda: "local-test-key")
		pages = [page_data(1, rotation=90), page_data(2)]
		response = Mock(status_code=200)
		response.json.return_value = {
			"choices": [{"message": {"content": json.dumps({"pages": pages})}, "finish_reason": "stop"}],
			"usage": {"prompt_tokens": 100, "completion_tokens": 300, "total_tokens": 400},
		}
		self.post.side_effect = None
		self.post.return_value = response
		with patch.object(llm, "ensure_ready", return_value=settings):
			self.assertEqual(get_page_data(self.scan.name), pages)
		self.post.assert_called_once()
		body = self.post.call_args.kwargs["json"]
		self.assertEqual(body["response_format"], {"type": "json_object"})
		self.assertEqual(len(body["messages"][1]["content"]), 3)
		log = frappe.get_last_doc("Document AI Call Log", filters={"model": "local/test-vision"})
		self.assertEqual(log.status, "Success")
		self.assertEqual(log.total_tokens, 400)
		self.assertEqual(json.loads(log.raw_response)["pages"], pages)

	def test_replaced_attachment_extracts_all_pages_once(self):
		get_page_data(self.scan.name)
		frappe.db.set_value(flow.DETAIL, self.details[0].name, "attachment", self.details[1].attachment)
		self.assertEqual(len(get_page_data(self.scan.name)), 2)
		self.assertEqual(self.vision_mock.call_count, 2)
		self.assertEqual(len(self.vision_mock.call_args.args[0]), 2)
		get_page_data(self.scan.name)
		self.assertEqual(self.vision_mock.call_count, 2)

	def test_changed_image_at_same_url_invalidates_cache(self):
		get_page_data(self.scan.name)
		from frappe_tools.scan_categorization.test_rotation import data_url

		changed = data_url(cv2.rotate(page_image(), cv2.ROTATE_180))
		with patch("frappe_tools.api.doc_classify.file_to_data_url", return_value=changed):
			get_page_data(self.scan.name)
		self.assertEqual(self.vision_mock.call_count, 2)

	def test_missing_page_cache_refreshes_whole_scan(self):
		get_page_data(self.scan.name)
		frappe.db.set_value(flow.DETAIL, self.details[1].name, "ai_page_data", None)
		get_page_data(self.scan.name)
		self.assertEqual(self.vision_mock.call_count, 2)
		self.assertEqual(len(self.vision_mock.call_args.args[0]), 2)

	def test_reordering_cached_pages_updates_indices_without_vision(self):
		get_page_data(self.scan.name)
		frappe.db.set_value(flow.DETAIL, self.details[0].name, "page_no", 3)
		pages = get_page_data(self.scan.name)
		self.assertEqual([p["page_no"] for p in pages], [1, 2])
		self.assertEqual(pages[0]["document_type"], "Delivery Challan")
		self.vision_mock.assert_called_once()

	def test_empty_scan_returns_no_pages(self):
		self.assertEqual(get_page_data(self.new_scan().name), [])
		self.vision_mock.assert_not_called()

	def test_deleted_pages_are_not_read(self):
		frappe.db.set_value(flow.DETAIL, self.details[1].name, "is_deleted", 1)
		self.vision_mock.return_value["data"]["pages"] = self.vision_mock.return_value["data"]["pages"][:1]
		flow.categorize_scan(self.scan.name)
		self.assertEqual(self.scan.reload().ai_check_status, "Done")
		self.assertEqual(len(self.vision_mock.call_args.args[0]), 1)
		self.assertEqual(self.details[1].reload().title, self.titles[0])

	def test_original_survives_manual_retry(self):
		original = self.details[0].attachment
		flow.categorize_scan(self.scan.name)
		frappe.db.set_value(flow.SCAN, self.scan.name, "ai_check_status", "Queued")
		flow.categorize_scan(self.scan.name)
		self.assertEqual(self.scan.reload().ai_check_status, "Done")
		self.assertEqual(self.details[0].reload().original_attachment, original)
		self.assertEqual(get_page_data(self.scan.name), [
			{**page, "rotation": 0} for page in self.vision_mock.return_value["data"]["pages"]
		])
		self.vision_mock.assert_called_once()

	def test_missing_layout_fails_without_vision(self):
		with patch.object(flow, "_layouts", side_effect=ValueError("No scanner layouts")), patch.object(frappe, "log_error"):
			flow.categorize_scan(self.scan.name)
		self.assertEqual(self.scan.reload().ai_check_status, "Failed")
		self.vision_mock.assert_not_called()

	def test_rq_dedup_does_not_leave_a_finished_scan_queued(self):
		frappe.db.set_value(flow.SCAN, self.scan.name, "ai_check_status", "Done")
		with patch.object(frappe, "enqueue", return_value=None):
			self.assertFalse(flow.run_ai_check(self.scan.name)["queued"])
		self.assertEqual(self.scan.reload().ai_check_status, "Done")

	def test_legacy_classifier_respects_human_review(self):
		from frappe_tools.api.doc_classify import classify_scanned_document

		flow.mark_reviewed(self.scan.name)
		with self.assertRaises(frappe.ValidationError):
			classify_scanned_document(self.scan.name)
		self.vision_mock.assert_not_called()

	def test_nightly_continues_after_an_enqueue_failure(self):
		self.settings.ai_process_since = "2099-01-01"
		for day in (1, 2):
			self.add_pages(self.new_scan(creation=f"2099-01-0{day}"))
		with patch.object(flow, "_queue_scan", side_effect=[RuntimeError("Queue unavailable"), True]), patch.object(frappe.db, "rollback"), patch.object(frappe, "log_error") as log:
			self.assertEqual(flow.enqueue_nightly(), 1)
		log.assert_called_once()
		self.assertTrue(frappe.db.exists(flow.SCAN, {"_docname": self.bill.name, "ai_check_status": "Failed"}))

	def test_settings_patch_initializes_only_missing_values(self):
		from frappe_tools.scan_categorization.settings_defaults import execute

		frappe.db.set_single_value(flow.SETTINGS, "ai_confidence_threshold", None)
		frappe.db.set_single_value(flow.SETTINGS, "ai_batch_limit", None)
		execute()
		self.assertEqual(frappe.db.get_single_value(flow.SETTINGS, "ai_confidence_threshold"), 0.8)
		self.assertEqual(frappe.db.get_single_value(flow.SETTINGS, "ai_batch_limit"), 200)
		frappe.db.set_single_value(flow.SETTINGS, "ai_confidence_threshold", 0)
		frappe.db.set_single_value(flow.SETTINGS, "ai_batch_limit", 10)
		execute()
		self.assertEqual(frappe.db.get_single_value(flow.SETTINGS, "ai_confidence_threshold"), 0)
		self.assertEqual(frappe.db.get_single_value(flow.SETTINGS, "ai_batch_limit"), 10)

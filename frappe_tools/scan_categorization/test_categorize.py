import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import frappe
from frappe.tests import UnitTestCase

from frappe_tools.extractors.pipeline import file_to_data_url
from frappe_tools.frappe_tools.doctype.scanned_document_ai_verifier import (
	scanned_document_ai_verifier as verifier,
)
from frappe_tools.scan_categorization import categorize as flow
from frappe_tools.scan_categorization import page_data as reader
from frappe_tools.scan_categorization.page_data import PAGE_SCHEMA, _extract_pages, get_page_data
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
		"other_fields": {"Payment terms": "30 days"},
		"item_table": {"columns": ["Description", "HSN", "Qty", "Roll No"],
			"rows": [{"Description": "Test fabric", "HSN": "5208", "Qty": "5.00", "Roll No": "R-42"}]},
		"handwritten": [{"kind": "signature", "text": "yes", "location": "bottom right"}],
		"full_text": "TAX INVOICE\nLocal Scanner Supplier\nTest fabric 5208 5.00 Nos 1,250.00",
		"language": "English", "confidence_notes": None, **values,
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
		self.pages = [page_data(1, rotation=90), page_data(2, document_type="Delivery Challan")]
		self.image_numbers = {}
		self.vision = patch.object(llm, "call_vision", side_effect=self.read_image)
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
			"scanner_layout": self.layout.name, "ai_check_status": status, "ai_reviewed": reviewed, "ai_run_id": frappe.generate_hash(length=16),
		}).insert()
		if creation:
			frappe.db.set_value(flow.SCAN, scan.name, "creation", creation)
		return scan

	def read_image(self, images, *args, **kwargs):
		self.assertEqual(len(images), 1)
		return {"data": {"pages": [{**self.pages[self.image_numbers[images[0]] - 1], "page_no": 1}]}}

	def store_pages(self, details):
		for detail, page in zip(details, self.pages, strict=True):
			attachment = frappe.db.get_value(flow.DETAIL, detail.name, "attachment")
			frappe.db.set_value(flow.DETAIL, detail.name, reader.page_data_values(page, attachment), update_modified=False)

	def clear_pages(self, details):
		for detail in details:
			frappe.db.set_value(flow.DETAIL, detail.name, {"ai_page_data": None, "ai_page_data_attachment": None})

	def returned(self, pages):
		return [{**page, "page_no": number, "section": self.titles[0]} for number, page in enumerate(pages, 1)]

	def add_pages(self, scan, read=True):
		image = page_image()
		details = []
		for number in (1, 2):
			image[0, 0] = [int(self.prefix[-6:-4], 16), int(self.prefix[-4:-2], 16), number]
			page = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE) if number == 1 else image
			content = cv2.imencode(".png", page)[1].tobytes()
			details.append(frappe.get_doc({
				"doctype": flow.DETAIL, "scanner_document": scan.name, "page_no": number,
				"layout_type": "Single Page", "page_type": "Front", "title": self.titles[0],
			}).insert())
			file = save_file_always_new(f"{self.prefix}-{scan.name}-{number}.png", content, flow.DETAIL, details[-1].name, is_private=1, df="attachment")
			details[-1].db_set("attachment", file.file_url)
			self.image_numbers[file_to_data_url(file.file_url)] = number
		if read:
			self.store_pages(details)
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
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
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
		self.assertTrue(frappe.db.exists("File", {"file_url": self.details[0].attachment, "attached_to_name": self.details[0].name, "attached_to_field": "attachment", "is_private": 1}))
		self.assertEqual(self.details[1].title, self.titles[1])
		self.assertEqual(self.details[1].layout_type, "Series Vertical")
		self.assertFalse(self.details[1].original_attachment)
		self.vision_mock.assert_not_called()
		self.assertEqual(self.post.call_count, 2)
		state = self.post.call_args.kwargs["json"]["state"]
		summaries = [{key: value for key, value in page.items() if key not in reader.RAW_FIELDS} for page in self.pages]
		for call in self.post.call_args_list:
			self.assertEqual(call.kwargs["json"]["state"]["pages"], summaries)
		self.assertIn("full_text", frappe.parse_json(self.details[1].ai_page_data))
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
			flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
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
		original_correction = flow.correct_image
		with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
			def assert_unlocked():
				for call in sql.call_args_list:
					query = str(call.args[0]).lower()
					if "tabscanned document" in query:
						self.assertNotIn("for update", query)
						self.assertFalse(query.lstrip().startswith(("update", "insert", "delete")), query)

			def response(*args, **kwargs):
				assert_unlocked()
				return self.response(*args, **kwargs)

			def correct(*args):
				assert_unlocked()
				return original_correction(*args)

			self.post.side_effect = response
			with patch.object(flow, "correct_image", side_effect=correct):
				flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
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
			"title": lambda: frappe.db.set_value(flow.DETAIL, self.details[0].name, "title", self.titles[1]),
			"layout_type": lambda: frappe.db.set_value(flow.DETAIL, self.details[0].name, "layout_type", "Series Vertical"),
			"page_type": lambda: frappe.db.set_value(flow.DETAIL, self.details[0].name, "page_type", "Back"),
			"source_type": lambda: frappe.db.set_value(flow.SCAN, self.scan.name, "_doctype", "Sales Invoice"),
			"source_name": lambda: frappe.db.set_value(flow.SCAN, self.scan.name, "_docname", "local-replaced"),
			"scanner_layout": lambda: frappe.db.set_value(flow.SCAN, self.scan.name, "scanner_layout", None),
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
					flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
				apply.assert_not_called()
				self.scan.reload()
				if label in ("reviewed", "status"):
					self.assertEqual(self.scan.ai_check_status, "Done")
				else:
					self.assertEqual(self.scan.ai_check_status, "Needs Review")
					self.assertIn("changed during AI processing", self.scan.ai_error)
				if label not in ("reviewed", "status"):
					_log, outcome = self.outcome()
					self.assertEqual(outcome["status"], self.scan.ai_check_status)
					self.assertFalse(outcome["applied"])
				self.assertEqual(self.scan.ai_reviewed, int(label == "reviewed"))
				self.assertTrue(self.details[1].reload().ai_page_data)
				self.assertEqual(self.details[1].title, self.titles[0])
				frappe.db.rollback(save_point="concurrent_change")
				self.scan.reload()

	def test_change_during_image_correction_is_rechecked(self):
		correct = flow.correct_image
		def change(*args):
			frappe.db.set_value(flow.DETAIL, self.details[0].name, "is_deleted", 1)
			return correct(*args)
		with patch.object(flow, "correct_image", side_effect=change):
			flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Needs Review")
		self.assertFalse(self.details[0].reload().original_attachment)

	def test_invalid_page_reply_stores_error_and_other_pages_continue(self):
		self.clear_pages(self.details)
		replies = iter([{"data": {"pages": [page_data(2)]}}, {"data": {"pages": [page_data(1)]}}])
		self.vision_mock.side_effect = lambda *args, **kwargs: next(replies)
		reader.extract_scan_pages(self.scan.name)
		self.assertFalse(self.details[0].reload().ai_page_data)
		self.assertIn("every page exactly once", self.details[0].ai_page_error)
		self.assertEqual(self.details[1].reload().ai_page_data_attachment, self.details[1].attachment)
		self.assertFalse(self.details[1].ai_page_error)

		reader.extract_scan_pages(self.scan.name)
		self.assertEqual(self.vision_mock.call_count, 2)
		self.assertNotIn(self.scan.name, [name for name, _count in reader._scans_with_unread_pages(None)])

	def test_page_questions_follow_active_detail_order(self):
		frappe.db.set_value(flow.DETAIL, self.details[0].name, "page_no", 5)
		frappe.db.set_value(flow.DETAIL, self.details[1].name, "page_no", 9)
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Done", self.scan.ai_error)
		for detail, title in zip(self.details, self.titles, strict=True):
			self.assertEqual(detail.reload().title, title)
		self.assertEqual(list(self.post.call_args.kwargs["json"]["questions"]), ["page_1", "page_2"])

	def test_corrected_jpeg_attachment_and_cache_keep_format(self):
		content = cv2.imencode(".jpg", cv2.rotate(page_image(), cv2.ROTATE_90_COUNTERCLOCKWISE))[1].tobytes()
		original = save_file_always_new(f"{self.prefix}.jpg", content, flow.SCAN, self.scan.name, is_private=1)
		frappe.db.set_value(flow.DETAIL, self.details[0].name, "attachment", original.file_url)
		self.store_pages(self.details)
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Done", self.scan.ai_error)
		detail = self.details[0].reload()
		self.assertEqual(detail.original_attachment, original.file_url)
		self.assertTrue(detail.attachment.endswith(".jpg"))
		file = frappe.get_doc("File", {"file_url": detail.attachment})
		self.assertTrue(file.get_content().startswith(b"\xff\xd8\xff"))
		self.assertEqual(get_page_data(self.scan.name)[0]["rotation"], 0)
		self.vision_mock.assert_not_called()

	def test_low_layout_confidence_and_exact_threshold(self):
		self.layout_confidence = 0.7
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Needs Review")
		frappe.db.set_value(flow.SCAN, self.scan.name, "ai_check_status", "Queued")
		self.layout_confidence = self.confidence = 0.8
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Done")

	def test_failure_records_error_and_does_not_escape(self):
		self.post.side_effect = RuntimeError("Local simulated API failure")
		with patch.object(frappe, "log_error") as log:
			flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Failed")
		self.assertIn("simulated API failure", self.scan.ai_error)
		log.assert_called_once()
		self.assertTrue(frappe.db.exists("Document AI Call Log", {"model": "typesafe/jev-1.13", "status": "Error"}))

	def test_partial_application_is_rolled_back(self):
		original = self.details[0].attachment
		self.pages[0]["rotation"] = 180
		self.store_pages(self.details)
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
			flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Failed")
		self.assertEqual(self.details[0].reload().attachment, original)
		self.assertFalse(self.details[0].original_attachment)
		self.assertEqual(len(files), 1)
		self.assertFalse(frappe.db.exists("File", files[0].name))
		self.assertFalse(Path(files[0].get_full_path()).exists())
		self.assertTrue(Path(frappe.get_doc("File", {"file_url": original}).get_full_path()).exists())
		_log, outcome = self.outcome()
		self.assertEqual(outcome["status"], "Failed")
		self.assertFalse(outcome["applied"])
		self.assertTrue(all(page["corrected_file"] is None and page["deskew_angle"] == 0 for page in outcome["pages"]))
		self.assertEqual([page["rotation"] for page in get_page_data(self.scan.name)], [180, 0])
		self.vision_mock.assert_not_called()

	def test_review_and_completed_jobs_are_never_overwritten(self):
		flow.mark_reviewed(self.scan.name)
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertFalse(flow.run_ai_check(self.scan.name)["queued"])
		self.post.assert_not_called()
		self.assertEqual(self.scan.reload().ai_reviewed, 1)
		for status in ("Done", "Needs Review", "Failed", ""):
			frappe.db.set_value(flow.SCAN, self.scan.name, {"ai_reviewed": 0, "ai_check_status": status})
			flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.post.assert_not_called()

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
			self.assertEqual(enqueue.call_args.kwargs["job_id"], f"scan-categorize::{first.name}::{first.reload().ai_run_id}")
			self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])
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
		self.vision_mock.side_effect = None
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
		self.vision_mock.side_effect = None
		full = page_data(1, items=[page_data(1)["items"][0]] * 30,
			other_fields={"Terms": "Printed terms " * 100})
		blank = {key: None for key in PAGE_SCHEMA}
		blank.update(page_no=2, rotation=0, is_blank_or_unreadable=True)
		self.vision_mock.return_value = {"data": {"pages": [full, blank]}}
		self.assertEqual(_extract_pages(["one", "two"], target_doctype="Purchase Invoice"), [full, blank])
		args = self.vision_mock.call_args
		self.assertEqual(args.args[0], ["one", "two"])
		self.assertEqual(args.kwargs["target_doctype"], "Purchase Invoice")
		self.assertIn("WHOLE content", args.args[1])
		self.assertIn("exactly as printed", args.args[2])
		self.assertEqual(args.kwargs["min_output_tokens"], reader.PAGE_OUTPUT_TOKENS)
		for field in PAGE_SCHEMA:
			self.assertIn(field, args.args[2])

	def test_invalid_vision_response_saves_error_and_no_page_data(self):
		self.clear_pages(self.details)
		self.vision_mock.side_effect = None
		for data in (None, [], {}, {"pages": [page_data(1), page_data(1)]}, {"pages": [page_data(2)]},
			{"pages": [page_data(1, items=["invalid row"])]}):
			with self.subTest(data=data):
				self.vision_mock.return_value = {"data": data}
				with self.assertRaisesRegex(ValueError, "could not be read"):
					get_page_data(self.scan.name)
				for detail in self.details:
					self.assertFalse(detail.reload().ai_page_data)
					self.assertTrue(detail.ai_page_error)

	def test_schema_keeps_full_text_and_printed_item_table(self):
		for key in ("full_text", "handwritten", "language", "confidence_notes"):
			self.assertIn(key, PAGE_SCHEMA)
		self.assertEqual(set(PAGE_SCHEMA["item_table"]), {"columns", "rows"})
		for table in ({"columns": ["Qty"]}, {"columns": ["Qty"], "rows": ["5"]}, []):
			with self.subTest(table=table), self.assertRaises(ValueError):
				reader._validate_pages([page_data(1, item_table=table)], 1)

	def test_truncated_response_saves_error_not_partial_data(self):
		self.clear_pages(self.details[:1])
		self.vision.stop()
		settings = frappe._dict(model="local/test-vision", max_attempts=1, get_api_key=lambda: "local-test-key")
		response = Mock(status_code=200)
		response.json.return_value = {"choices": [{"message": {"content": json.dumps({"pages": self.pages[:1]})},
			"finish_reason": "length"}]}
		self.post.side_effect = None
		self.post.return_value = response
		with patch.object(llm, "ensure_ready", return_value=settings), self.assertRaises(ValueError):
			get_page_data(self.scan.name)
		self.assertFalse(self.details[0].reload().ai_page_data)
		self.assertIn("finish_reason=length", self.details[0].ai_page_error)

	def test_each_page_is_read_once_in_its_own_call_and_saved(self):
		self.clear_pages(self.details)
		pages = get_page_data(self.scan.name)
		self.assertEqual(pages, self.returned(self.pages))
		self.assertEqual(self.vision_mock.call_count, 2)
		for call in self.vision_mock.call_args_list:
			self.assertEqual(call.kwargs["phase"], "Page Extraction")
			self.assertEqual(call.kwargs["reference_name"], self.scan.name)
		for page, detail in zip(self.pages, self.details, strict=True):
			detail.reload()
			self.assertEqual(frappe.parse_json(detail.ai_page_data), {**page, "page_no": 1})
			self.assertEqual(detail.ai_page_data_attachment, detail.attachment)
			self.assertTrue(detail.ai_page_data_on)
		self.assertEqual(get_page_data(self.scan.name), pages)
		self.assertEqual(self.vision_mock.call_count, 2)
		self.post.assert_not_called()

	def test_read_holds_no_lock_during_vision_and_skips_a_replaced_page(self):
		self.clear_pages(self.details)
		with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
			def vision(images, *args, **kwargs):
				queries = [str(call.args[0]).lower() for call in sql.call_args_list]
				self.assertFalse([query for query in queries if "for update" in query or query.lstrip().startswith("update")])
				frappe.db.set_value(flow.DETAIL, self.details[0].name, "attachment", "/private/files/local-replaced.png")
				return self.read_image(images)

			self.vision_mock.side_effect = vision
			reader.read_page(frappe._dict(name=self.scan.name, _doctype="Purchase Invoice"), self.details[0])
		self.assertFalse(self.details[0].reload().ai_page_data)
		self.assertFalse(self.details[0].ai_page_error)

	def test_stored_data_is_kept_when_categorization_needs_review(self):
		self.clear_pages(self.details)
		reader.extract_scan_pages(self.scan.name)
		self.layout_confidence = 0.1
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Needs Review")
		self.assertEqual(get_page_data(self.scan.name), self.returned(self.pages))
		self.assertEqual(self.vision_mock.call_count, 2)

	def test_vision_uses_json_mode_and_logs_full_response(self):
		self.vision.stop()
		self.clear_pages(self.details[:1])
		settings = frappe._dict(model="local/test-vision", max_attempts=1, max_tokens=8000,
			request_timeout=30, get_api_key=lambda: "local-test-key")
		response = Mock(status_code=200)
		response.json.return_value = {
			"choices": [{"message": {"content": json.dumps({"pages": self.pages[:1]})}, "finish_reason": "stop"}],
			"usage": {"prompt_tokens": 100, "completion_tokens": 300, "total_tokens": 400},
		}
		self.post.side_effect = None
		self.post.return_value = response
		with patch.object(llm, "ensure_ready", return_value=settings):
			self.assertEqual(get_page_data(self.scan.name), self.returned(self.pages))
		self.post.assert_called_once()
		body = self.post.call_args.kwargs["json"]
		self.assertEqual(body["response_format"], {"type": "json_object"})
		self.assertEqual(body["max_tokens"], 16000)
		self.assertEqual(len(body["messages"][1]["content"]), 2)
		log = frappe.get_last_doc("Document AI Call Log", filters={"model": "local/test-vision"})
		self.assertEqual(log.status, "Success")
		self.assertEqual(log.total_tokens, 400)
		self.assertEqual(json.loads(log.raw_response)["pages"], self.pages[:1])

	def test_replaced_attachment_reads_only_that_page(self):
		content = cv2.imencode(".png", page_image())[1].tobytes()
		file = save_file_always_new(f"{self.prefix}-new.png", content, flow.DETAIL, self.details[1].name, is_private=1)
		self.image_numbers[file_to_data_url(file.file_url)] = 2
		frappe.db.set_value(flow.DETAIL, self.details[1].name, "attachment", file.file_url)
		self.assertEqual(get_page_data(self.scan.name), self.returned(self.pages))
		self.vision_mock.assert_called_once()
		self.assertEqual(self.details[1].reload().ai_page_data_attachment, file.file_url)
		get_page_data(self.scan.name)
		self.vision_mock.assert_called_once()

	def test_same_attachment_is_copied_without_vision(self):
		scan = self.new_scan()
		copies = self.add_pages(scan, read=False)
		for copy, detail in zip(copies, self.details, strict=True):
			frappe.db.set_value(flow.DETAIL, copy.name, "attachment", detail.attachment)
		reader.extract_scan_pages(scan.name)
		self.vision_mock.assert_not_called()
		for copy, page in zip(copies, self.pages, strict=True):
			copy.reload()
			self.assertEqual(frappe.parse_json(copy.ai_page_data), page)
			self.assertEqual(copy.ai_page_data_attachment, copy.attachment)

	def test_reordering_stored_pages_updates_indices_without_vision(self):
		frappe.db.set_value(flow.DETAIL, self.details[0].name, "page_no", 3)
		pages = get_page_data(self.scan.name)
		self.assertEqual([p["page_no"] for p in pages], [1, 2])
		self.assertEqual(pages[0]["document_type"], "Delivery Challan")
		self.vision_mock.assert_not_called()

	def test_empty_scan_returns_no_pages(self):
		self.assertEqual(get_page_data(self.new_scan().name), [])
		self.vision_mock.assert_not_called()

	def test_page_data_needs_print_permission_on_linked_document(self):
		with (
			patch.object(frappe, "has_permission", side_effect=frappe.PermissionError) as permission,
			self.assertRaises(frappe.PermissionError),
		):
			get_page_data(self.scan.name)
		permission.assert_called_once_with("Purchase Invoice", "print", doc=self.bill.name, throw=True)
		self.vision_mock.assert_not_called()

	def test_deleted_pages_are_not_used(self):
		frappe.db.set_value(flow.DETAIL, self.details[1].name, "is_deleted", 1)
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Done")
		self.assertEqual(list(self.post.call_args.kwargs["json"]["questions"]), ["page_1"])
		self.assertEqual(self.details[1].reload().title, self.titles[0])
		self.vision_mock.assert_not_called()

	def test_original_survives_manual_retry(self):
		original = self.details[0].attachment
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		frappe.db.set_value(flow.SCAN, self.scan.name, "ai_check_status", "Queued")
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Done")
		self.assertEqual(self.details[0].reload().original_attachment, original)
		self.assertEqual([page["rotation"] for page in get_page_data(self.scan.name)], [0, 0])
		self.vision_mock.assert_not_called()

	def test_categorization_skips_scans_with_unread_pages(self):
		self.settings.ai_process_since = "2099-01-01"
		self.settings.ai_batch_limit = 1
		unread = self.new_scan(creation="2099-01-01")
		self.add_pages(unread, read=False)
		ready = self.new_scan(creation="2099-01-02")
		self.add_pages(ready)
		with patch.object(frappe, "enqueue") as enqueue:
			self.assertEqual(flow.enqueue_nightly(), 1)
			self.assertEqual(enqueue.call_args.kwargs["name"], ready.name)
			with self.assertRaisesRegex(frappe.ValidationError, "not read yet"):
				flow.run_ai_check(unread.name)
		self.assertFalse(unread.reload().ai_check_status)

		self.clear_pages(self.details[1:])
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertFalse(self.scan.reload().ai_check_status)
		self.assertIn("not read yet", self.scan.ai_error)
		self.vision_mock.assert_not_called()
		self.post.assert_not_called()

	def test_hourly_extraction_selects_unread_pages_up_to_limit(self):
		settings = frappe._dict(enable_page_extraction=1, page_extraction_batch_limit=2,
			ai_process_since="2099-01-01")
		old = self.new_scan(creation="2098-12-31")
		self.add_pages(old, read=False)
		first = self.new_scan(creation="2099-01-01")
		self.add_pages(first, read=False)
		second = self.new_scan(creation="2099-01-02")
		self.add_pages(second, read=False)
		self.add_pages(self.new_scan(creation="2099-01-01"))
		failed = self.new_scan(creation="2099-01-01")
		for detail in self.add_pages(failed, read=False):
			frappe.db.set_value(flow.DETAIL, detail.name, "ai_page_error", "Local read failed")
		deleted = self.new_scan(creation="2099-01-01")
		for detail in self.add_pages(deleted, read=False):
			frappe.db.set_value(flow.DETAIL, detail.name, "is_deleted", 1)
		with (
			patch.object(frappe, "get_single", return_value=settings),
			patch.object(reader, "is_job_enqueued", return_value=False),
			patch.object(frappe, "enqueue") as enqueue,
		):
			self.assertEqual(reader.enqueue_page_extraction(), 1)
			self.assertEqual(enqueue.call_args.kwargs["name"], first.name)
			self.assertEqual(enqueue.call_args.kwargs["job_id"], f"scan-page-extraction::{first.name}")
			self.assertFalse(enqueue.call_args.kwargs["retry_failed"])
			settings.page_extraction_batch_limit = 3
			self.assertEqual(reader.enqueue_page_extraction(), 2)
			self.assertEqual(enqueue.call_args.kwargs["name"], second.name)
			settings.enable_page_extraction = 0
			self.assertEqual(reader.enqueue_page_extraction(), 0)
		self.assertEqual(enqueue.call_count, 3)
		self.vision_mock.assert_not_called()

	def test_read_pages_now_needs_scan_permission_and_dedups(self):
		with (
			patch.object(frappe, "get_roles", return_value=["Scanner User"]),
			patch.object(frappe, "has_permission", return_value=False),
			self.assertRaises(frappe.PermissionError),
		):
			flow.read_pages_now(self.scan.name)
		with (
			patch.object(frappe, "enqueue") as enqueue,
			patch.object(reader, "is_job_enqueued", side_effect=[False, True]),
		):
			self.assertTrue(flow.read_pages_now(self.scan.name)["queued"])
			self.assertFalse(flow.read_pages_now(self.scan.name)["queued"])
		enqueue.assert_called_once()
		self.assertTrue(enqueue.call_args.kwargs["retry_failed"])

	def test_verifier_uses_stored_pages_and_reads_only_missing_ones(self):
		self.clear_pages(self.details[1:])
		pages, omitted = verifier._load_pages([self.scan.name])
		self.assertEqual(omitted, 0)
		self.assertEqual(pages, [{"scanned_document": self.scan.name, **page} for page in self.returned(self.pages)])
		self.vision_mock.assert_called_once()
		self.assertEqual(self.vision_mock.call_args.args[0], [file_to_data_url(self.details[1].attachment)])
		verifier._load_pages([self.scan.name])
		self.vision_mock.assert_called_once()

	def test_missing_layout_fails_without_vision(self):
		with patch.object(flow, "_layouts", side_effect=ValueError("No scanner layouts")), patch.object(frappe, "log_error"):
			flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Failed")
		self.vision_mock.assert_not_called()

	def test_rerun_uses_a_new_job_id_after_completion(self):
		frappe.db.set_value(flow.SCAN, self.scan.name, "ai_check_status", "Done")
		old_run = self.scan.ai_run_id
		with patch.object(frappe, "enqueue", return_value=None) as enqueue:
			self.assertTrue(flow.run_ai_check(self.scan.name)["queued"])
		self.assertNotEqual(self.scan.reload().ai_run_id, old_run)
		self.assertEqual(enqueue.call_args.kwargs["run_id"], self.scan.ai_run_id)

	def test_legacy_classifier_respects_human_review(self):
		from frappe_tools.api.doc_classify import classify_scanned_document

		flow.mark_reviewed(self.scan.name)
		with self.assertRaises(frappe.ValidationError):
			classify_scanned_document(self.scan.name)
		self.vision_mock.assert_not_called()

	def test_settings_patch_initializes_only_missing_values(self):
		from frappe_tools.patches.set_scan_ai_setting_defaults import execute

		frappe.db.set_single_value(flow.SETTINGS, "ai_confidence_threshold", None)
		frappe.db.set_single_value(flow.SETTINGS, "ai_batch_limit", None)
		frappe.db.set_single_value(flow.SETTINGS, "ai_max_pages", None)
		frappe.db.set_single_value(flow.SETTINGS, "page_extraction_batch_limit", None)
		execute()
		self.assertEqual(frappe.db.get_single_value(flow.SETTINGS, "ai_confidence_threshold"), 0.8)
		self.assertEqual(frappe.db.get_single_value(flow.SETTINGS, "ai_batch_limit"), 200)
		self.assertEqual(frappe.db.get_single_value(flow.SETTINGS, "ai_max_pages"), 30)
		self.assertEqual(frappe.db.get_single_value(flow.SETTINGS, "page_extraction_batch_limit"), 500)
		frappe.db.set_single_value(flow.SETTINGS, "ai_confidence_threshold", 0)
		frappe.db.set_single_value(flow.SETTINGS, "ai_batch_limit", 10)
		execute()
		self.assertEqual(frappe.db.get_single_value(flow.SETTINGS, "ai_confidence_threshold"), 0)
		self.assertEqual(frappe.db.get_single_value(flow.SETTINGS, "ai_batch_limit"), 10)

	def outcome(self):
		logs = frappe.get_all(llm.CALL_LOG_DOCTYPE, filters={
			"reference_doctype": flow.SCAN, "reference_name": self.scan.name,
			"phase": "Categorization Outcome",
		}, fields=["name", "run_id", "provider", "model", "response_payload"])
		self.assertEqual(len(logs), 1)
		log = logs[0]
		self.assertEqual(log.provider, "frappe_tools")
		self.assertFalse(log.model)
		self.assertEqual(len(log.run_id), 16)
		return log, json.loads(log.response_payload)

	def test_all_phases_share_reference_and_run_id(self):
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		log, outcome = self.outcome()
		self.assertEqual(outcome["status"], "Done")
		self.assertTrue(outcome["applied"])
		self.assertEqual(outcome["threshold"], 0.8)
		self.assertEqual(outcome["layout"], self.layout.name)
		self.assertEqual(outcome["layout_confidence"], 0.95)
		self.assertIsNone(outcome["layout_probabilities"])
		self.assertEqual(outcome["reason"], "")
		for index, (page, detail) in enumerate(zip(outcome["pages"], self.details, strict=True)):
			original = detail.attachment
			detail.reload()
			self.assertEqual(page, {
				"detail": detail.name, "page_no": index + 1, "section": self.titles[index],
				"section_confidence": 0.9, "rotation": 90 if index == 0 else 0,
				"deskew_angle": 0.0, "corrected_file": detail.attachment if index == 0 else None,
				"original_attachment": original,
			})
		logs = frappe.get_all(llm.CALL_LOG_DOCTYPE, filters={"run_id": log.run_id},
			fields=["phase", "reference_doctype", "reference_name"])
		self.assertCountEqual([row.phase for row in logs], [
			"Layout Decision", "Section Decision", "Categorization Outcome",
		])
		for row in logs:
			self.assertEqual((row.reference_doctype, row.reference_name), (flow.SCAN, self.scan.name))

	def test_review_outcome_preserves_proposals_without_corrections(self):
		self.confidence = 0.7
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		_log, outcome = self.outcome()
		self.assertEqual(outcome["status"], "Needs Review")
		self.assertFalse(outcome["applied"])
		self.assertEqual(outcome["threshold"], 0.8)
		self.assertEqual(outcome["layout"], self.layout.name)
		self.assertEqual(outcome["layout_confidence"], 0.95)
		self.assertIn("below threshold", outcome["reason"])
		self.vision_mock.assert_not_called()
		for index, page in enumerate(outcome["pages"]):
			self.assertEqual(page["section"], self.titles[index])
			self.assertEqual(page["section_confidence"], 0.7)
			self.assertIsNone(page["corrected_file"])
			self.assertEqual(page["deskew_angle"], 0.0)

	def test_failed_outcome_is_logged_once(self):
		self.post.side_effect = RuntimeError("Local provider failure")
		with patch.object(frappe, "log_error"):
			flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		_log, outcome = self.outcome()
		self.assertEqual(outcome["status"], "Failed")
		self.assertFalse(outcome["applied"])
		self.assertEqual(outcome["reason"], "Local provider failure")
		self.assertEqual(len(outcome["pages"]), 2)

	def test_verifier_persists_phase_logs_with_run_id(self):
		self.vision.stop()
		from frappe_tools.frappe_tools.doctype.scanned_document_ai_verifier import (
			scanned_document_ai_verifier as verifier,
		)

		doc = frappe.get_doc({
			"doctype": verifier.DOCTYPE, "reference_doctype": "Purchase Invoice",
			"filters_data": json.dumps([["name", "=", self.bill.name]]),
			"prompt": "Compare total quantity with the scanned quantity.", "max_documents": 1,
		}).insert()
		self.vision.stop()
		settings = frappe._dict(model="local/test-verifier", max_attempts=1, get_api_key=lambda: "local-test-key")
		responses = []
		for data in ({"columns": [{"key": "quantity", "label": "Quantity"}]},
			{"values": {"quantity": 5}, "remarks": "Matches"}):
			response = Mock(status_code=200)
			response.json.return_value = {"choices": [{"message": {"content": json.dumps(data)}}]}
			responses.append(response)
		self.post.side_effect = responses
		with patch.object(llm, "ensure_ready", return_value=settings), patch.object(frappe, "enqueue"):
			verifier.run_verifier(doc.name)
			verifier.process_verifier(doc.name, "Administrator", doc.reload().last_run_id)
		doc.reload()
		self.assertEqual(doc.status, "Completed", doc.error_log)
		self.assertEqual(doc.processed_documents, 1)
		self.assertEqual(doc.failed_documents, 0)
		self.assertEqual(len(doc.last_run_id), 16)
		logs = frappe.get_all(llm.CALL_LOG_DOCTYPE, filters={
			"reference_doctype": verifier.DOCTYPE, "reference_name": doc.name,
		}, fields=["phase", "run_id", "request_payload", "provider"])
		self.assertCountEqual([log.phase for log in logs], ["Verifier Columns", "Verifier Row", "Verifier Row"])
		for log in logs:
			self.assertEqual(log.run_id, doc.last_run_id)
			if log.phase == "Verifier Row" and log.provider != "frappe_tools":
				payload = json.loads(log.request_payload)
				evidence = json.loads(payload["messages"][1]["content"][0]["text"])
				self.assertEqual(evidence["document"]["name"], self.bill.name)
				self.assertEqual(evidence["document"]["total_qty"], 5)
		from io import BytesIO

		from openpyxl import load_workbook

		file = frappe.get_doc("File", {"file_url": doc.result_file})
		self.assertTrue(file.is_private)
		workbook = load_workbook(BytesIO(file.get_content()))
		self.addCleanup(workbook.close)
		self.assertEqual(list(workbook.active.values)[1][-1], 5)

	def test_vision_legacy_call_and_retry_metadata(self):
		import requests

		self.vision.stop()
		settings = frappe._dict(model="local/test-compatibility", max_attempts=2, get_api_key=lambda: "local-test-key")
		response = Mock(status_code=200)
		response.json.return_value = {"choices": [{"message": {"content": '{"quantity": 5}'}}]}
		context = {"phase": "Page Extraction", "reference_doctype": flow.SCAN,
			"reference_name": self.scan.name, "run_id": frappe.generate_hash(length=12)}
		self.post.side_effect = [response, requests.Timeout("Local timeout"), response]
		with patch.object(llm, "ensure_ready", return_value=settings), patch.object(llm.time, "sleep"):
			self.assertEqual(llm.call_vision([], "Read", "Quantity")["data"], {"quantity": 5})
			self.assertEqual(llm.call_vision([], "Read", "Quantity", **context)["data"], {"quantity": 5})
		logs = frappe.get_all(llm.CALL_LOG_DOCTYPE, filters={"model": settings.model},
			fields=["phase", "reference_doctype", "reference_name", "run_id", "status"], order_by="creation asc")
		self.assertEqual(len(logs), 3)
		self.assertTrue(all(not logs[0][key] for key in context))
		self.assertEqual([row.status for row in logs], ["Success", "Timeout", "Success"])
		for row in logs[1:]:
			self.assertEqual({key: row[key] for key in context}, context)

	def test_outcome_records_probabilities_and_applied_deskew(self):
		probabilities = {self.layout.name: 0.95}
		def response(*args, **kwargs):
			result = self.response(*args, **kwargs)
			if "layout" in result.json.return_value["answers"]:
				result.json.return_value["answers"]["layout"]["probabilities"] = probabilities
			return result

		self.post.side_effect = response
		with patch("frappe_tools.scan_categorization.rotation.detect_skew", return_value=2.5):
			flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		_log, outcome = self.outcome()
		self.assertEqual(outcome["layout_probabilities"], probabilities)
		self.assertEqual(outcome["status"], "Done")
		self.assertTrue(all(page["deskew_angle"] == 2.5 and page["corrected_file"] for page in outcome["pages"]))

	def test_original_survives_scanner_save_and_hourly_cleanup(self):
		from frappe_tools.api.doc_scanner import make_or_update_main_doc, upload_image
		from frappe_tools.frappe_tools.doctype.scanned_document_detail.scanned_document_detail import (
			remove_old_deletable_documents,
		)

		original = frappe.get_doc("File", {"file_url": self.details[0].attachment})
		content = original.get_content()
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Done")
		replacements = [upload_image(detail.reload().as_dict()) for detail in self.details]
		make_or_update_main_doc(self.scan._doctype, self.scan.scanner_layout, self.scan._docname,
			scan_name=self.scan.name, documents=replacements)
		# Exercise cleanup's retention guard even for legacy ownership left on an old row.
		frappe.db.set_value("File", original.name, "attached_to_name", self.details[0].name)
		get_all = frappe.get_all
		def local_details(doctype, *args, **kwargs):
			if doctype == flow.DETAIL and kwargs.get("filters") == {"is_deleted": 1}:
				kwargs["filters"]["name"] = ["in", [detail.name for detail in self.details]]
			return get_all(doctype, *args, **kwargs)
		with patch.object(frappe, "get_all", side_effect=local_details):
			remove_old_deletable_documents()
		self.assertFalse(frappe.db.exists(flow.DETAIL, self.details[0].name))
		self.assertEqual(original.reload().get_content(), content)
		self.assertEqual(original.attached_to_doctype, flow.DETAIL)
		self.assertEqual(original.attached_to_name, replacements[0])
		self.assertEqual(original.attached_to_field, "original_attachment")
		self.assertEqual(frappe.db.get_value(flow.DETAIL, replacements[0], "original_attachment"), original.file_url)

	def test_stale_token_never_writes_outcome(self):
		old_run = self.scan.ai_run_id
		new_run = frappe.generate_hash(length=16)
		def replace_run(*args, **kwargs):
			frappe.db.set_value(flow.SCAN, self.scan.name, {"ai_run_id": new_run, "ai_error": "new run"})
			return self.response(*args, **kwargs)
		self.post.side_effect = replace_run
		flow.categorize_scan(self.scan.name, old_run)
		self.assertEqual(self.scan.reload().ai_check_status, "Queued")
		self.assertEqual(self.scan.ai_error, "new run")
		self.assertFalse(self.details[0].reload().original_attachment)
		self.assertFalse(frappe.db.exists("Document AI Call Log", {
			"reference_name": self.scan.name, "phase": "Categorization Outcome", "run_id": old_run,
		}))
		self.post.reset_mock()
		flow.categorize_scan(self.scan.name, old_run)
		self.post.assert_not_called()

	def test_expired_queue_recovery_manual_and_nightly(self):
		from frappe.utils import add_to_date, now_datetime

		self.settings.ai_process_since = "2099-01-01"
		frappe.db.set_value(flow.SCAN, self.scan.name, "creation", "2099-01-01")
		for recover in (lambda: flow.run_ai_check(self.scan.name)["queued"], flow.enqueue_nightly):
			with self.subTest(recover=recover):
				old_run = self.scan.reload().ai_run_id
				frappe.db.set_value(flow.SCAN, self.scan.name, "modified", add_to_date(now_datetime(), hours=-7), update_modified=False)
				with patch.object(flow, "is_job_enqueued", return_value=True), patch.object(frappe, "enqueue") as enqueue:
					self.assertFalse(recover())
					enqueue.assert_not_called()
				with patch.object(flow, "is_job_enqueued", return_value=False), patch.object(frappe, "enqueue") as enqueue:
					self.assertTrue(recover())
					self.assertNotEqual(self.scan.reload().ai_run_id, old_run)
					self.assertEqual(enqueue.call_args.kwargs["run_id"], self.scan.ai_run_id)
					self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])

	def test_page_limit_requires_review_without_ai(self):
		self.settings.ai_max_pages = 1
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Needs Review")
		self.assertIn("2 pages", self.scan.ai_error)
		self.vision_mock.assert_not_called()
		self.post.assert_not_called()

	def test_section_positions_match_scanner_shapes(self):
		for kind in ("Series Vertical", "Front And Back Vertical", "Front And Back Horizontal"):
			with self.subTest(kind=kind):
				answers = {f"page_{i}": {"choice": "section"} for i in range(1, 5)}
				positions, reason = flow._positions({"section": kind}, answers)
				self.assertFalse(reason)
				paired = kind.startswith("Front And Back")
				self.assertEqual([p["page_no"] for p in positions], [1, 1, 2, 2] if paired else [1, 2, 3, 4])
				self.assertEqual([p["page_type"] for p in positions], ["Front", "Back"] * 2 if paired else ["Front"] * 4)
		positions, reason = flow._positions({"section": "Single Page"}, answers)
		self.assertFalse(positions)
		self.assertIn("only one page", reason)

	def test_legacy_classifier_checks_snapshot_and_permissions(self):
		from frappe_tools.api.doc_classify import classify_scanned_document, page_classifier

		with (
			patch.object(frappe, "get_roles", return_value=["Scanner User"]),
			patch.object(frappe, "has_permission", return_value=False),
			self.assertRaises(frappe.PermissionError),
		):
			classify_scanned_document(self.scan.name)
		def classify(*args):
			frappe.db.set_value(flow.DETAIL, self.details[0].name, "title", self.titles[1])
			return {"pages": []}
		with (
			patch.object(page_classifier, "classify", side_effect=classify),
			self.assertRaisesRegex(frappe.ValidationError, "changed during AI processing"),
		):
			classify_scanned_document(self.scan.name)
		self.assertEqual(self.details[0].reload().title, self.titles[1])

	def test_vision_worker_timeout_is_not_retried(self):
		from rq.timeouts import JobTimeoutException

		self.vision.stop()
		settings = frappe._dict(model="local/test-timeout", max_attempts=3, get_api_key=lambda: "local-key")
		self.post.side_effect = JobTimeoutException("local worker deadline")
		with patch.object(llm, "ensure_ready", return_value=settings), self.assertRaises(JobTimeoutException):
			llm.call_vision([], "Read", "Quantity")
		self.post.assert_called_once()

	def test_single_page_overflow_does_not_apply(self):
		def choose_single(*args, **kwargs):
			response = self.response(*args, **kwargs)
			for key, answer in response.json.return_value["answers"].items():
				if key.startswith("page_"):
					answer["choice"] = self.titles[0]
			return response
		self.post.side_effect = choose_single
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Needs Review")
		self.assertIn("only one page", self.scan.ai_error)
		self.assertFalse(self.details[0].reload().original_attachment)

	def test_queue_state_is_committed_before_publication(self):
		frappe.db.set_value(flow.SCAN, self.scan.name, "ai_check_status", "Failed")
		events = []
		def enqueue(*args, **kwargs):
			events.append("enqueue")
			current = self.scan.reload()
			self.assertEqual(current.ai_check_status, "Queued")
			self.assertEqual(current.ai_run_id, kwargs["run_id"])
			self.assertTrue(kwargs["enqueue_after_commit"])
		with patch.object(frappe.db, "commit", side_effect=lambda: events.append("commit")), patch.object(frappe, "enqueue", side_effect=enqueue):
			self.assertTrue(flow.run_ai_check(self.scan.name)["queued"])
		self.assertEqual(events, ["commit", "enqueue", "commit"])

	def test_decision_criteria_name_layout_and_sections(self):
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		layout_question = self.post.call_args_list[0].kwargs["json"]["questions"]["layout"]
		criterion = layout_question["criteria"][self.layout.name]
		self.assertIn(f"Layout '{self.layout.name}' for Purchase Invoice", criterion)
		self.assertIn(f"{self.titles[0]} (Single Page)", criterion)
		self.assertIn(f"{self.titles[1]} (Series Vertical)", criterion)
		page_question = self.post.call_args.kwargs["json"]["questions"]["page_2"]
		self.assertIn("state.pages[1]", page_question["instructions"])
		self.assertEqual(page_question["criteria"][self.titles[1]],
			f"Section '{self.titles[1]}' of layout '{self.layout.name}'. Shape: Series Vertical (several pages in order)")

	def test_nightly_continues_after_a_queue_failure(self):
		self.settings.ai_process_since = "2099-01-01"
		for day in (1, 2):
			self.add_pages(self.new_scan(creation=f"2099-01-0{day}"))
		with (
			patch.object(flow, "_queue_scan", side_effect=[RuntimeError("Queue unavailable"), True]),
			patch.object(frappe.db, "rollback"), patch.object(frappe, "log_error") as log,
		):
			self.assertEqual(flow.enqueue_nightly(), 1)
		log.assert_called_once()

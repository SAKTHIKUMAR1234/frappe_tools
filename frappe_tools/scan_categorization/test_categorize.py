import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import frappe
from frappe.tests import UnitTestCase

from frappe_tools.ai.test_providers import response as provider_response
from frappe_tools.api.doc_classify import load_classification_pages
from frappe_tools.extractors.pipeline import file_to_data_url
from frappe_tools.file_data import extraction
from frappe_tools.file_data import reader as file_reader
from frappe_tools.file_data.documents import files_by_url, files_for_document, get_document_pages
from frappe_tools.file_data.extraction import PAGE_SCHEMA, extract_pages
from frappe_tools.frappe_tools.doctype.document_extraction_settings.document_extraction_settings import (
	DEFAULT_PROMPTS,
)
from frappe_tools.frappe_tools.doctype.scanned_document_ai_verifier import (
	scanned_document_ai_verifier as verifier,
)
from frappe_tools.scan_categorization import categorize as flow
from frappe_tools.scan_categorization import decisions
from frappe_tools.scan_categorization import page_data as reader
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


def scan_data(call) -> dict:
	return json.loads(call.args[2].split("Scan data (JSON):\n", 1)[1])


def page_keys(call) -> list[str]:
	return list(call.kwargs["schema"]["properties"])


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
		self.http = patch("requests.post")
		self.post = self.http.start()
		self.addCleanup(self.http.stop)
		self.decision = patch.object(decisions, "call_model", side_effect=self.decide)
		self.model = self.decision.start()
		self.addCleanup(self.decision.stop)
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

	def file(self, detail):
		attachment = frappe.db.get_value(flow.DETAIL, detail.name, "attachment")
		return files_by_url([attachment])[attachment]

	def data(self, detail):
		data = self.file(detail).ai_page_data
		return frappe.parse_json(data) if data else None

	def set_file(self, detail, values):
		attachment = frappe.db.get_value(flow.DETAIL, detail.name, "attachment")
		for name in frappe.get_all("File", filters={"file_url": attachment}, pluck="name"):
			frappe.db.set_value("File", name, values, update_modified=False)

	def store_pages(self, details):
		for detail, page in zip(details, self.pages, strict=True):
			self.set_file(detail, file_reader.data_values({"pages": [{**page, "page_no": 1}]}, "local/test"))

	def clear_pages(self, details):
		for detail in details:
			self.set_file(detail, {"ai_page_data": None})

	def scan_pages(self, name):
		return get_document_pages(flow.SCAN, name)

	def returned(self, pages):
		return [{**page, "page_no": number, "section": self.titles[0]} for number, page in enumerate(pages, 1)]

	def add_pages(self, scan, read=True):
		image = page_image()
		details = []
		for number in (1, 2):
			image[0, 0] = [int(self.prefix[-6:-4], 16), int(self.prefix[-4:-2], 16), number]
			image[0, 1] = [len(self.image_numbers) % 256, int(self.prefix[-2:], 16), 0]
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

	def decide(self, task, system_prompt, user_prompt, **kwargs):
		keys = kwargs["schema"]["properties"]
		if "layout" in keys:
			return {"data": {"layout": self.layout.name, "confidence": self.layout_confidence, "reason": "Local test"}}
		return {"data": {key: {"section": self.titles[int(key.split("_")[1]) - 1], "confidence": self.confidence} for key in keys}}

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
		self.post.assert_not_called()
		self.assertEqual(self.model.call_count, 2)
		state = scan_data(self.model.call_args)
		summaries = [{key: value for key, value in page.items() if key not in extraction.RAW_FIELDS} for page in self.pages]
		for call in self.model.call_args_list:
			self.assertEqual(call.args[0], "Scan Decision")
			self.assertEqual(call.kwargs["target_doctype"], "Purchase Invoice")
			self.assertEqual(scan_data(call)["pages"], summaries)
		self.assertIn("full_text", self.data(self.details[1])["pages"][0])
		self.assertEqual(self.data(self.details[0])["pages"], [{**self.pages[0], "rotation": 0, "page_no": 1}])
		self.assertEqual(float(state["document"]["grand_total"]), 1250)
		self.assertEqual(float(state["document"]["items"][0]["amount"]), 1250)
		self.assertEqual(page_keys(self.model.call_args), ["page_1", "page_2"])

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
			self.assertTrue(self.data(detail))
			self.assertTrue(frappe.db.get_value("File", self.file(detail).name, "ai_page_data_on"))

	def test_external_calls_and_correction_precede_scan_locks_and_writes(self):
		original_correction = flow.correct_image
		with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
			def assert_unlocked():
				for call in sql.call_args_list:
					query = str(call.args[0]).lower()
					if "tabscanned document" in query:
						self.assertNotIn("for update", query)
						self.assertFalse(query.lstrip().startswith(("update", "insert", "delete")), query)

			def decide(*args, **kwargs):
				assert_unlocked()
				return self.decide(*args, **kwargs)

			def correct(*args):
				assert_unlocked()
				return original_correction(*args)

			self.model.side_effect = decide
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
				def decide(*args, **kwargs):
					if "page_1" in kwargs["schema"]["properties"]:
						change()
					return self.decide(*args, **kwargs)
				self.model.side_effect = decide
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
				self.assertTrue(self.data(self.details[1]))
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
		self.assertFalse(self.data(self.details[0]))
		self.assertIn("every page exactly once", self.file(self.details[0]).ai_page_error)
		self.assertTrue(self.data(self.details[1]))
		self.assertFalse(self.file(self.details[1]).ai_page_error)

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
		self.assertEqual(page_keys(self.model.call_args), ["page_1", "page_2"])

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
		self.assertEqual(self.scan_pages(self.scan.name)[0]["rotation"], 0)
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
		self.model.side_effect = RuntimeError("Local simulated API failure")
		with patch.object(frappe, "log_error") as log:
			flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Failed")
		self.assertIn("simulated API failure", self.scan.ai_error)
		log.assert_called_once()

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
		self.assertEqual([page["rotation"] for page in self.scan_pages(self.scan.name)], [180, 0])
		self.vision_mock.assert_not_called()

	def test_review_and_completed_jobs_are_never_overwritten(self):
		flow.mark_reviewed(self.scan.name)
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertFalse(flow.run_ai_check(self.scan.name)["queued"])
		self.model.assert_not_called()
		self.assertEqual(self.scan.reload().ai_reviewed, 1)
		for status in ("Done", "Needs Review", "Failed", ""):
			frappe.db.set_value(flow.SCAN, self.scan.name, {"ai_reviewed": 0, "ai_check_status": status})
			flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.model.assert_not_called()

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
					extract_pages(["image"], target_doctype="Purchase Invoice")

	def test_vision_prompt_and_full_response(self):
		self.vision_mock.side_effect = None
		full = page_data(1, items=[page_data(1)["items"][0]] * 30,
			other_fields={"Terms": "Printed terms " * 100})
		blank = {key: None for key in PAGE_SCHEMA}
		blank.update(page_no=2, rotation=0, is_blank_or_unreadable=True)
		self.vision_mock.return_value = {"data": {"pages": [full, blank]}}
		self.assertEqual(extract_pages(["one", "two"], target_doctype="Purchase Invoice"), ([full, blank], None))
		args = self.vision_mock.call_args
		self.assertEqual(args.args[0], ["one", "two"])
		self.assertEqual(args.kwargs["target_doctype"], "Purchase Invoice")
		self.assertIn("WHOLE content", args.args[1])
		self.assertIn("exactly as printed", args.args[1])
		self.assertTrue(args.args[1].endswith(extraction.PAGE_SAFETY_RULES))
		self.assertEqual(args.kwargs["min_output_tokens"], extraction.PAGE_OUTPUT_TOKENS)
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
					self.scan_pages(self.scan.name)
				for detail in self.details:
					self.assertFalse(self.data(detail))
					self.assertTrue(self.file(detail).ai_page_error)

	def test_schema_keeps_full_text_and_printed_item_table(self):
		for key in ("full_text", "handwritten", "language", "confidence_notes"):
			self.assertIn(key, PAGE_SCHEMA)
		self.assertEqual(set(PAGE_SCHEMA["item_table"]), {"columns", "rows"})
		for table in ({"columns": ["Qty"]}, {"columns": ["Qty"], "rows": ["5"]}, []):
			with self.subTest(table=table), self.assertRaises(ValueError):
				extraction._validate_pages([page_data(1, item_table=table)], 1)

	def test_truncated_response_saves_error_not_partial_data(self):
		self.clear_pages(self.details[:1])
		self.vision.stop()
		settings = frappe._dict(model="local/test-vision", max_attempts=1, get_api_key=lambda: "local-test-key")
		response = provider_response({"choices": [{"message": {"content": json.dumps({"pages": self.pages[:1]})},
			"finish_reason": "length"}]})
		self.post.side_effect = None
		self.post.return_value = response
		with patch.object(llm, "ensure_ready", return_value=settings), self.assertRaises(ValueError):
			self.scan_pages(self.scan.name)
		self.assertFalse(self.data(self.details[0]))
		self.assertIn("finish_reason=length", self.file(self.details[0]).ai_page_error)

	def test_each_page_is_read_once_in_its_own_call_and_saved(self):
		self.clear_pages(self.details)
		pages = self.scan_pages(self.scan.name)
		self.assertEqual(pages, self.returned(self.pages))
		self.assertEqual(self.vision_mock.call_count, 2)
		for call in self.vision_mock.call_args_list:
			self.assertEqual(call.kwargs["phase"], "Page Extraction")
			self.assertEqual(call.kwargs["reference_name"], self.scan.name)
		for page, detail in zip(self.pages, self.details, strict=True):
			file = self.file(detail)
			self.assertEqual(frappe.parse_json(file.ai_page_data), {"pages": [{**page, "page_no": 1}]})
			self.assertEqual(file.ai_page_count, 1)
			self.assertTrue(frappe.db.get_value("File", file.name, "ai_page_data_on"))
		self.assertEqual(self.scan_pages(self.scan.name), pages)
		self.assertEqual(self.vision_mock.call_count, 2)
		self.post.assert_not_called()

	def test_read_holds_no_lock_during_vision_and_skips_a_changed_file(self):
		self.clear_pages(self.details)
		file = self.file(self.details[0])
		with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
			def vision(images, *args, **kwargs):
				queries = [str(call.args[0]).lower() for call in sql.call_args_list]
				self.assertFalse([query for query in queries if "for update" in query or query.lstrip().startswith("update")])
				frappe.db.set_value("File", file.name, "content_hash", "local-replaced-hash")
				return self.read_image(images)

			self.vision_mock.side_effect = vision
			self.assertIsNone(file_reader.read_file(file.name))
			queries = [str(call.args[0]).lower() for call in sql.call_args_list]
			self.assertTrue(any("tabfile`" in query and "for update" in query for query in queries))
		self.assertFalse(frappe.db.get_value("File", file.name, "ai_page_data"))
		self.assertFalse(frappe.db.get_value("File", file.name, "ai_page_error"))

	def test_stored_data_is_kept_when_categorization_needs_review(self):
		self.clear_pages(self.details)
		reader.extract_scan_pages(self.scan.name)
		self.layout_confidence = 0.1
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Needs Review")
		self.assertEqual(self.scan_pages(self.scan.name), self.returned(self.pages))
		self.assertEqual(self.vision_mock.call_count, 2)

	def test_vision_uses_json_mode_and_logs_full_response(self):
		self.vision.stop()
		self.clear_pages(self.details[:1])
		settings = frappe._dict(model="local/test-vision", max_attempts=1, max_tokens=8000,
			request_timeout=30, get_api_key=lambda: "local-test-key")
		response = provider_response({
			"choices": [{"message": {"content": json.dumps({"pages": self.pages[:1]})}, "finish_reason": "stop"}],
			"usage": {"prompt_tokens": 100, "completion_tokens": 300, "total_tokens": 400},
		})
		self.post.side_effect = None
		self.post.return_value = response
		with patch.object(llm, "ensure_ready", return_value=settings):
			self.assertEqual(self.scan_pages(self.scan.name), self.returned(self.pages))
		self.post.assert_called_once()
		self.assertEqual(self.file(self.details[0]).ai_page_model, "OpenRouter/local/test-vision")
		body = self.post.call_args.kwargs["json"]
		self.assertEqual(body["response_format"], {"type": "json_object"})
		self.assertEqual(body["max_tokens"], 16000)
		self.assertEqual(len(body["messages"][1]["content"]), 2)
		log = frappe.get_last_doc("Document AI Call Log", filters={"model": "OpenRouter/local/test-vision"})
		self.assertEqual(log.status, "Success")
		self.assertEqual(log.total_tokens, 400)
		self.assertEqual(json.loads(log.raw_response)["pages"], self.pages[:1])

	def test_replaced_attachment_reads_only_that_page(self):
		content = cv2.imencode(".png", page_image())[1].tobytes()
		file = save_file_always_new(f"{self.prefix}-new.png", content, flow.DETAIL, self.details[1].name, is_private=1)
		self.image_numbers[file_to_data_url(file.file_url)] = 2
		frappe.db.set_value(flow.DETAIL, self.details[1].name, "attachment", file.file_url)
		self.assertEqual(self.scan_pages(self.scan.name), self.returned(self.pages))
		self.vision_mock.assert_called_once()
		self.assertEqual(self.file(self.details[1]).file_url, file.file_url)
		self.assertTrue(self.data(self.details[1]))
		self.scan_pages(self.scan.name)
		self.vision_mock.assert_called_once()

	def test_same_attachment_is_copied_without_vision(self):
		scan = self.new_scan()
		copies = self.add_pages(scan, read=False)
		for copy, detail in zip(copies, self.details, strict=True):
			frappe.db.set_value(flow.DETAIL, copy.name, "attachment", detail.attachment)
		reader.extract_scan_pages(scan.name)
		self.vision_mock.assert_not_called()
		self.assertFalse(reader.has_unread_pages(scan.name))
		self.assertEqual(self.scan_pages(scan.name), self.returned(self.pages))

	def test_reordering_stored_pages_updates_indices_without_vision(self):
		frappe.db.set_value(flow.DETAIL, self.details[0].name, "page_no", 3)
		pages = self.scan_pages(self.scan.name)
		self.assertEqual([p["page_no"] for p in pages], [1, 2])
		self.assertEqual(pages[0]["document_type"], "Delivery Challan")
		self.vision_mock.assert_not_called()

	def test_scan_pages_follow_page_order_with_sections(self):
		frappe.db.set_value(flow.DETAIL, self.details[0].name, {"page_no": 3, "title": self.titles[1]})
		pages = self.scan_pages(self.scan.name)
		self.assertEqual([(page["page_no"], page["section"], page["document_type"]) for page in pages],
			[(1, self.titles[0], "Delivery Challan"), (2, self.titles[1], "Tax Invoice")])
		self.assertEqual(files_for_document(flow.SCAN, self.scan.name),
			[self.file(detail).name for detail in reversed(self.details)])

	def test_empty_scan_returns_no_pages(self):
		self.assertEqual(get_document_pages(flow.SCAN, self.new_scan().name), [])
		self.vision_mock.assert_not_called()

	def test_page_data_needs_read_permission_on_linked_document(self):
		with (
			patch.object(frappe, "has_permission", side_effect=frappe.PermissionError) as permission,
			self.assertRaises(frappe.PermissionError),
		):
			self.scan_pages(self.scan.name)
		permission.assert_called_once_with("Purchase Invoice", "read", doc=self.bill.name, throw=True)
		self.vision_mock.assert_not_called()

	def test_deleted_pages_are_not_used(self):
		frappe.db.set_value(flow.DETAIL, self.details[1].name, "is_deleted", 1)
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Done")
		self.assertEqual(page_keys(self.model.call_args), ["page_1"])
		self.assertEqual(self.details[1].reload().title, self.titles[0])
		self.vision_mock.assert_not_called()

	def test_original_survives_manual_retry(self):
		original = self.details[0].attachment
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		frappe.db.set_value(flow.SCAN, self.scan.name, "ai_check_status", "Queued")
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Done")
		self.assertEqual(self.details[0].reload().original_attachment, original)
		self.assertEqual([page["rotation"] for page in self.scan_pages(self.scan.name)], [0, 0])
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
		self.model.assert_not_called()

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
			self.set_file(detail, {"ai_page_error": "Local read failed",
				"ai_page_error_on": frappe.utils.now_datetime(), "ai_page_attempts": 1})
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

	def fail_page(self, attempts, hours_ago):
		self.set_file(self.details[0], {
			"ai_page_data": None, "ai_page_error": "Local read failed", "ai_page_attempts": attempts,
			"ai_page_error_on": frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-hours_ago),
		})

	def selected(self):
		return dict(reader._scans_with_unread_pages(None)).get(self.scan.name, 0)

	def test_failed_page_is_retried_after_delay_and_resets_attempts(self):
		self.fail_page(attempts=2, hours_ago=7)
		self.assertEqual(self.selected(), 1)
		reader.extract_scan_pages(self.scan.name)
		self.vision_mock.assert_called_once()
		file = self.file(self.details[0])
		self.assertTrue(file.ai_page_data)
		self.assertEqual((file.ai_page_attempts, file.ai_page_error, file.ai_page_error_on), (0, None, None))

	def test_failed_page_is_not_retried_before_delay(self):
		self.fail_page(attempts=1, hours_ago=5)
		self.assertEqual(self.selected(), 0)
		reader.extract_scan_pages(self.scan.name)
		self.vision_mock.assert_not_called()

	def test_failed_reads_stop_after_three_attempts(self):
		self.fail_page(attempts=2, hours_ago=7)
		self.vision_mock.side_effect = RuntimeError("Local provider failure")
		reader.extract_scan_pages(self.scan.name)
		file = self.file(self.details[0])
		self.assertEqual(file.ai_page_attempts, 3)
		self.assertIn("Local provider failure", file.ai_page_error)
		self.fail_page(attempts=3, hours_ago=7)
		self.assertEqual(self.selected(), 0)
		reader.extract_scan_pages(self.scan.name)
		self.vision_mock.assert_called_once()

	def test_manual_retry_ignores_attempt_cap_and_delay(self):
		self.fail_page(attempts=3, hours_ago=0)
		reader.extract_scan_pages(self.scan.name, retry_failed=True)
		self.vision_mock.assert_called_once()
		self.assertEqual(self.file(self.details[0]).ai_page_attempts, 0)

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
		pages = verifier._load_pages("Purchase Invoice", self.bill.name, [self.scan.name])
		self.assertEqual(pages, [{"scanned_document": self.scan.name, **page} for page in self.returned(self.pages)])
		self.vision_mock.assert_called_once()
		self.assertEqual(self.vision_mock.call_args.args[0], [file_to_data_url(self.details[1].attachment)])
		verifier._load_pages("Purchase Invoice", self.bill.name, [self.scan.name])
		self.vision_mock.assert_called_once()

	def test_catch_all_layout_is_never_an_option(self):
		catch_all = frappe.get_doc({
			"doctype": "Document Scanner Layout", "layout_name": self.prefix + "-all", "layout_doctype": "Purchase Invoice",
			"is_catch_all": 1, "layout_doctype_sections": [{"title": self.titles[0], "layout_type": "Single Page"}],
		}).insert()
		layouts = flow._layouts("Purchase Invoice")
		self.assertIn(self.layout.name, layouts)
		self.assertNotIn(catch_all.name, layouts)

	def test_layout_hints_give_notes_and_party_history(self):
		frappe.db.set_value("Document Scanner Layout", self.layout.name, "ai_description", "Fabric bills")
		frappe.db.set_value("Purchase Invoice", self.bill.name, "supplier", "Local Scanner Supplier")
		self.new_scan()
		hints = flow._layout_hints(self.scan, {self.layout.name: []})
		self.assertEqual(hints["layout_notes"], {self.layout.name: "Fabric bills"})
		self.assertEqual(hints["party_layout_history"], {self.layout.name: 1})

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
		frappe.db.set_single_value(flow.SETTINGS, "page_extraction_batch_limit", None)
		execute()
		self.assertEqual(frappe.db.get_single_value(flow.SETTINGS, "ai_confidence_threshold"), 0.8)
		self.assertEqual(frappe.db.get_single_value(flow.SETTINGS, "ai_batch_limit"), 200)
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
		self.assertNotIn("layout_probabilities", outcome)
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
		context = {"reference_doctype": flow.SCAN, "reference_name": self.scan.name, "run_id": log.run_id}
		self.assertEqual([call.kwargs["phase"] for call in self.model.call_args_list], ["Layout Decision", "Section Decision"])
		for call in self.model.call_args_list:
			self.assertEqual({key: call.kwargs[key] for key in context}, context)
		logs = frappe.get_all(llm.CALL_LOG_DOCTYPE, filters={"run_id": log.run_id},
			fields=["phase", "reference_doctype", "reference_name"])
		self.assertEqual([(row.phase, row.reference_doctype, row.reference_name) for row in logs],
			[("Categorization Outcome", flow.SCAN, self.scan.name)])

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
		self.model.side_effect = RuntimeError("Local provider failure")
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
		settings = frappe.get_single("Document Extraction Settings")
		settings.update(dict(enable=1, model="local/test-verifier", max_attempts=1, task_models=[]))
		self.enterContext(patch.object(settings, "get_api_key", return_value="local-test-key"))
		responses = []
		for data in ({"columns": [{"key": "quantity", "label": "Quantity"}]},
			{"values": {"quantity": 5}, "remarks": "Matches"}):
			responses.append(provider_response({"choices": [{"message": {"content": json.dumps(data)}}]}))
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
		self.assertCountEqual([log.phase for log in logs], ["Verifier Columns", "Verifier Agent Step", "Verifier Row"])
		for log in logs:
			self.assertEqual(log.run_id, doc.last_run_id)
			if log.phase == "Verifier Agent Step":
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
		response = provider_response({"choices": [{"message": {"content": '{"quantity": 5}'}}]})
		context = {"phase": "Page Extraction", "reference_doctype": flow.SCAN,
			"reference_name": self.scan.name, "run_id": frappe.generate_hash(length=12)}
		self.post.side_effect = [response, requests.Timeout("Local timeout"), response]
		with patch.object(llm, "ensure_ready", return_value=settings), patch("frappe_tools.ai.client.time.sleep"):
			self.assertEqual(llm.call_vision([], "Read", "Quantity")["data"], {"quantity": 5})
			self.assertEqual(llm.call_vision([], "Read", "Quantity", **context)["data"], {"quantity": 5})
		logs = frappe.get_all(llm.CALL_LOG_DOCTYPE, filters={"model": f"OpenRouter/{settings.model}"},
			fields=["phase", "reference_doctype", "reference_name", "run_id", "status"], order_by="creation asc")
		self.assertEqual(len(logs), 3)
		self.assertTrue(all(not logs[0][key] for key in context))
		self.assertEqual([row.status for row in logs], ["Success", "Timeout", "Success"])
		for row in logs[1:]:
			self.assertEqual({key: row[key] for key in context}, context)

	def test_outcome_records_applied_deskew(self):
		with patch("frappe_tools.scan_categorization.rotation.detect_skew", return_value=2.5):
			flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		_log, outcome = self.outcome()
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
		replacements = [upload_image({**detail.reload().as_dict(), "detail": detail.name, "attachment": None})
			for detail in self.details]
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

	def scanner_page(self, detail=None, attachment=None):
		return {"page_no": 1, "page_type": "Front", "layout_type": "Single Page", "title": self.titles[0],
			"detail": detail, "attachment": attachment}

	def empty_slot(self, title, layout_type, page_no):
		return frappe.get_doc({
			"doctype": flow.DETAIL, "scanner_document": self.scan.name, "page_no": page_no,
			"layout_type": layout_type, "page_type": "Front", "title": title,
		}).insert()

	def test_reopened_scan_gives_each_page_its_detail(self):
		from frappe_tools.api.doc_scanner import load_scanned_document_details

		attachments = load_scanned_document_details(self.scan.name)["attachments"]
		self.assertEqual({row["detail"]: row["file_url"] for row in attachments},
			{detail.name: detail.attachment for detail in self.details})

	def test_resave_keeps_the_current_files_of_a_moved_page(self):
		from frappe_tools.api.doc_scanner import upload_image

		# A file migration changes the stored URLs after the scanner loaded the page.
		frappe.db.set_value(flow.DETAIL, self.details[0].name,
			{"attachment": "/private/files/local-moved.png", "original_attachment": "/private/files/local-original.png"})
		saved = upload_image(self.scanner_page(detail=self.details[0].name))
		self.assertEqual(frappe.db.get_value(flow.DETAIL, saved, ["attachment", "original_attachment"]),
			("/private/files/local-moved.png", "/private/files/local-original.png"))

	def test_resave_of_a_stale_page_is_refused(self):
		from frappe_tools.api.doc_scanner import upload_image

		stale_url = self.details[0].attachment
		frappe.db.set_value(flow.DETAIL, self.details[0].name, "attachment", "/private/files/local-moved.png")
		with self.assertRaisesRegex(frappe.ValidationError, "Reload the scan"):
			upload_image(self.scanner_page(attachment=stale_url))
		frappe.db.set_value(flow.DETAIL, self.details[1].name, "is_deleted", 1)
		with self.assertRaisesRegex(frappe.ValidationError, "Reload the scan"):
			upload_image(self.scanner_page(detail=self.details[1].name))

	def test_resave_needs_print_permission_on_the_page_document(self):
		from frappe_tools.api.doc_scanner import upload_image

		with (
			patch.object(frappe, "has_permission", return_value=False) as permission,
			self.assertRaises(frappe.PermissionError),
		):
			upload_image(self.scanner_page(detail=self.details[0].name))
		permission.assert_called_once_with("Purchase Invoice", doc=self.bill.name, ptype="print")

	def test_empty_scanner_slot_is_not_a_page(self):
		self.empty_slot(self.titles[0], "Single Page", 3)
		self.assertFalse(reader.has_unread_pages(self.scan.name))
		self.assertEqual(len(self.scan_pages(self.scan.name)), 2)
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Done")

	def test_categorization_retires_empty_slots_at_the_pages_it_places(self):
		from frappe_tools.api.doc_scanner import load_scanned_document_details

		taken = self.empty_slot(self.titles[1], "Series Vertical", 1)
		free = self.empty_slot(self.titles[1], "Series Vertical", 2)
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		self.assertEqual(self.scan.reload().ai_check_status, "Done", self.scan.ai_error)
		self.assertEqual((taken.reload().is_deleted, free.reload().is_deleted), (1, 0))
		positions = [(row["title"], row["page_no"], row["page_type"])
			for row in load_scanned_document_details(self.scan.name)["attachments"]]
		self.assertEqual(len(positions), len(set(positions)))

	def test_legacy_classifier_retires_empty_slots_at_the_pages_it_places(self):
		from frappe_tools.api.doc_classify import classify_scanned_document, page_classifier

		taken = self.empty_slot(self.titles[1], "Series Vertical", 1)
		free = self.empty_slot(self.titles[1], "Series Vertical", 3)
		pages = [{"section": self.titles[1], "page_type": "Front"}] * 2
		with patch.object(page_classifier, "classify", return_value={"pages": pages}):
			classify_scanned_document(self.scan.name)
		self.assertEqual((taken.reload().is_deleted, free.reload().is_deleted), (1, 0))
		self.assertEqual(self.details[0].reload().title, self.titles[1])
		self.assertEqual(self.details[0].layout_type, "Series Vertical")

	def test_load_lists_an_attached_page_before_an_empty_slot_at_its_position(self):
		from frappe_tools.api.doc_scanner import load_scanned_document_details

		self.empty_slot(self.titles[0], "Single Page", 1)
		rows = load_scanned_document_details(self.scan.name)["attachments"]
		self.assertEqual(rows[0]["detail"], self.details[0].name)
		self.assertIsNone(rows[1]["attachment"])

	def test_front_is_read_before_back_of_the_same_page(self):
		for detail, side in zip(self.details, ("Back", "Front"), strict=True):
			frappe.db.set_value(flow.DETAIL, detail.name, {"page_no": 1, "page_type": side})
		self.assertEqual(files_for_document(flow.SCAN, self.scan.name),
			[self.file(detail).name for detail in reversed(self.details)])
		self.assertEqual([detail.name for detail in load_classification_pages(self.scan.name)[0]],
			[detail.name for detail in reversed(self.details)])

	def test_stale_token_never_writes_outcome(self):
		old_run = self.scan.ai_run_id
		new_run = frappe.generate_hash(length=16)
		def replace_run(*args, **kwargs):
			frappe.db.set_value(flow.SCAN, self.scan.name, {"ai_run_id": new_run, "ai_error": "new run"})
			return self.decide(*args, **kwargs)
		self.model.side_effect = replace_run
		flow.categorize_scan(self.scan.name, old_run)
		self.assertEqual(self.scan.reload().ai_check_status, "Queued")
		self.assertEqual(self.scan.ai_error, "new run")
		self.assertFalse(self.details[0].reload().original_attachment)
		self.assertFalse(frappe.db.exists("Document AI Call Log", {
			"reference_name": self.scan.name, "phase": "Categorization Outcome", "run_id": old_run,
		}))
		self.model.reset_mock()
		flow.categorize_scan(self.scan.name, old_run)
		self.model.assert_not_called()

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

	def test_section_positions_match_scanner_shapes(self):
		for kind in ("Series Vertical", "Front And Back Vertical", "Front And Back Horizontal"):
			with self.subTest(kind=kind):
				titles = ["section"] * 4
				positions, reason = flow._positions({"section": kind}, titles)
				self.assertFalse(reason)
				paired = kind.startswith("Front And Back")
				self.assertEqual([p["page_no"] for p in positions], [1, 1, 2, 2] if paired else [1, 2, 3, 4])
				self.assertEqual([p["page_type"] for p in positions], ["Front", "Back"] * 2 if paired else ["Front"] * 4)
		positions, reason = flow._positions({"section": "Single Page"}, titles)
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
			result = self.decide(*args, **kwargs)
			for key, answer in result["data"].items():
				if key.startswith("page_"):
					answer["section"] = self.titles[0]
			return result
		self.model.side_effect = choose_single
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

	def test_decision_prompts_and_schemas_name_layout_and_sections(self):
		flow.categorize_scan(self.scan.name, self.scan.ai_run_id)
		layout_call, section_call = self.model.call_args_list
		self.assertIn("not instructions", layout_call.args[1])
		self.assertIn(f"Layout '{self.layout.name}' for Purchase Invoice", layout_call.args[2])
		self.assertIn(f"{self.titles[0]} (Single Page)", layout_call.args[2])
		self.assertIn(f"{self.titles[1]} (Series Vertical)", layout_call.args[2])
		self.assertIn(self.layout.name, layout_call.kwargs["schema"]["properties"]["layout"]["enum"])
		self.assertEqual(layout_call.kwargs["phase"], "Layout Decision")
		self.assertIn(f"'{self.titles[1]}': Series Vertical (several pages in order)", section_call.args[2])
		self.assertEqual(section_call.kwargs["phase"], "Section Decision")
		self.assertEqual(scan_data(section_call)["layout"], self.layout.name)
		page = section_call.kwargs["schema"]["properties"]["page_2"]
		self.assertEqual(page["properties"]["section"]["enum"], self.titles)
		self.assertEqual(page["required"], ["section", "confidence"])
		self.assertFalse(page["additionalProperties"])

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


class TestDecisions(UnitTestCase):
	def setUp(self):
		self.enterContext(patch.object(decisions, "MAX_INPUT_TOKENS", 26000))  # small budget keeps size tests fast
		self.layouts = {"Bill": [{"title": "Invoice", "layout_type": "Single Page"}]}
		self.sections = {"Invoice": "Single Page", "Receipt": "Series Vertical"}
		self.answer = {"layout": "Bill", "confidence": 0.91, "reason": "Tax invoice"}
		self.patch = patch.object(decisions, "call_model", return_value={"data": self.answer})
		self.model = self.patch.start()
		self.addCleanup(self.patch.stop)

	def sections_reply(self, *answers):
		self.model.return_value = {"data": {f"page_{i}": answer for i, answer in enumerate(answers, 1)}}

	def test_layout_request_and_answer(self):
		context = {"reference_doctype": "Scanned Document", "reference_name": "LOCAL-SCAN", "run_id": "local-run-id"}
		answer = decisions.decide_layout({"bill_no": "LOCAL-42"}, self.layouts, "Purchase Invoice", **context)
		self.assertEqual(answer, self.answer)
		call = self.model.call_args
		self.assertEqual(call.args[0], "Scan Decision")
		self.assertEqual(call.args[1], f"{DEFAULT_PROMPTS['Scan Decision']}\n\n{decisions.DECISION_RULES}")
		self.assertEqual(scan_data(call), {"bill_no": "LOCAL-42"})
		self.assertEqual(call.kwargs, {**context, "phase": "Layout Decision", "target_doctype": "Purchase Invoice",
			"schema": call.kwargs["schema"]})
		schema = call.kwargs["schema"]
		self.assertEqual(schema["properties"]["layout"]["enum"], ["Bill"])
		self.assertEqual(schema["required"], ["layout", "confidence", "reason"])
		self.assertFalse(schema["additionalProperties"])

	def test_section_answers_follow_page_order(self):
		self.sections_reply({"section": "Invoice", "confidence": 1}, {"section": "Receipt", "confidence": 0.5})
		answers = decisions.decide_sections({"pages": [{}, {}]}, "Bill", self.sections, "Purchase Invoice")
		self.assertEqual(answers, [{"section": "Invoice", "confidence": 1}, {"section": "Receipt", "confidence": 0.5}])
		self.assertEqual(page_keys(self.model.call_args), ["page_1", "page_2"])

	def test_invalid_layout_answers_fail_closed(self):
		for answer in ({}, [], {**self.answer, "layout": "Other"}, {**self.answer, "layout": ["Bill"]},
			{**self.answer, "confidence": -1}, {**self.answer, "confidence": 1.01},
			{**self.answer, "confidence": float("nan")}, {**self.answer, "confidence": True},
			{**self.answer, "confidence": "0.9"}, {**self.answer, "reason": None}):
			with self.subTest(answer=answer):
				self.model.return_value = {"data": answer}
				with self.assertRaises(ValueError):
					decisions.decide_layout({}, self.layouts, "Purchase Invoice")

	def test_every_page_needs_one_valid_answer(self):
		valid = {"section": "Invoice", "confidence": 0.9}
		for answers in ((valid,), (valid, valid, valid), (valid, {**valid, "section": "Other"}),
			(valid, {**valid, "confidence": float("inf")}), (valid, {"section": "Receipt"})):
			with self.subTest(answers=answers):
				self.sections_reply(*answers)
				with self.assertRaises(ValueError):
					decisions.decide_sections({"pages": [{}, {}]}, "Bill", self.sections, "Purchase Invoice")

	def test_oversized_state_never_calls_model(self):
		state = {"text": "x" * int(decisions.MAX_INPUT_TOKENS * 3.5)}
		with self.assertRaisesRegex(ValueError, "token budget after shrinking"):
			decisions.decide_layout(state, self.layouts, "Purchase Invoice")
		self.model.assert_not_called()

	def test_large_state_within_budget_is_sent_whole(self):
		state = {"text": "Printed invoice data " * 3000}
		self.assertLess(decisions.estimated_tokens(state, ""), decisions.MAX_INPUT_TOKENS)
		decisions.decide_layout(state, self.layouts, "Purchase Invoice")
		self.assertEqual(scan_data(self.model.call_args), state)

	def test_small_state_preserves_all_summary_page_data(self):
		page = {key: value for key, value in page_data(1).items() if key not in extraction.RAW_FIELDS}
		state = {"document": {"bill_no": "LOCAL-42"}, "pages": [page]}
		self.assertEqual(decisions.bounded_state(state, ""), state)

	def test_size_guard_order_and_preserved_fields(self):
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
				self.assertGreater(decisions.estimated_tokens(state, ""), decisions.MAX_INPUT_TOKENS)
				bounded = decisions.bounded_state(state, "")
				self.assertLessEqual(decisions.estimated_tokens(bounded, ""), decisions.MAX_INPUT_TOKENS)
				self.assertEqual(state, original)
				self.assertEqual(bounded["document"], state["document"])
				result = bounded["pages"][0]
				self.assertEqual(result["items"], page["items"][:15])
				self.assertEqual(result["items_truncated"], 25)
				if stage == "items":
					self.assertEqual(result["other_fields"], page["other_fields"])
				else:
					self.assertNotIn("other_fields", result)
				address = page["parties"]["supplier"]["address"]
				self.assertEqual(result["parties"]["supplier"]["address"], address[:200] if stage == "addresses" else address)
				for key in ("document_type", "document_number", "document_date", "references", "taxes", "totals"):
					self.assertEqual(result[key], page[key])

	def test_guard_counts_request_and_protects_essential_data(self):
		self.assertRaises(ValueError, decisions.bounded_state, {"pages": [{"references": {"irn": "x" * 92000}}]}, "")
		self.assertRaises(ValueError, decisions.bounded_state, {}, "x" * 92000)
		layouts = {f"Layout {'x' * 1000} {index}": [{"title": "Invoice", "layout_type": "Single Page"}] for index in range(90)}
		with self.assertRaises(ValueError):
			decisions.decide_layout({}, layouts, "Purchase Invoice")
		self.model.assert_not_called()

	def test_guard_keeps_every_page_and_only_counts_omitted_rows(self):
		pages = [page_data(1, items=None), page_data(2), page_data(3, items=[{"qty": "2"}] * 40)]
		pages[0]["other_fields"] = {"Terms": "x" * 92000}
		result = decisions.bounded_state({"pages": pages}, "")["pages"]
		self.assertEqual([page["page_no"] for page in result], [1, 2, 3])
		self.assertIsNone(result[0]["items"])
		self.assertEqual(result[1]["items"], pages[1]["items"])
		self.assertNotIn("items_truncated", result[0])
		self.assertNotIn("items_truncated", result[1])
		self.assertEqual(result[2]["items_truncated"], 25)
		self.assertEqual(len(result[2]["items"]), 15)

	def test_model_receives_bounded_state_without_trimming_saved_input(self):
		state = {"pages": [{"items": [{"description": "x" * 3000}] * 40, "other_fields": None}]}
		decisions.decide_layout(state, self.layouts, "Purchase Invoice")
		self.assertEqual(len(scan_data(self.model.call_args)["pages"][0]["items"]), 15)
		self.assertEqual(len(state["pages"][0]["items"]), 40)

	def test_raw_page_fields_are_always_dropped(self):
		pages = [page_data(1)]
		result = decisions.bounded_state({"pages": pages}, "")["pages"][0]
		for field in extraction.RAW_FIELDS:
			self.assertNotIn(field, result)
			self.assertIn(field, pages[0])
		self.assertEqual(result["items"], pages[0]["items"])

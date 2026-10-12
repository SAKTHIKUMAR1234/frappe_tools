import json
from unittest.mock import Mock, patch

import cv2
import frappe
from frappe.tests import UnitTestCase

from frappe_tools.classifiers.page_classifier import summarize
from frappe_tools.scan_categorization import categorize as flow
from frappe_tools.scan_categorization.test_rotation import page_image
from frappe_tools.utils import llm, save_file_always_new


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
			{"page": 1, "summary": "LOCAL invoice 42, total 1250", "rotation": 90},
			{"page": 2, "summary": "LOCAL delivery receipt", "rotation": 0},
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
		self.assertTrue(frappe.db.exists("File", {"file_url": original}))
		self.assertTrue(frappe.db.exists("File", {"file_url": self.details[0].attachment, "attached_to_name": self.scan.name, "is_private": 1}))
		self.assertEqual(self.details[1].title, self.titles[1])
		self.assertEqual(self.details[1].layout_type, "Series Vertical")
		self.assertFalse(self.details[1].original_attachment)
		self.vision_mock.assert_called_once()
		self.assertEqual(len(self.vision_mock.call_args.args[0]), 2)
		self.assertEqual(self.post.call_count, 2)
		state = self.post.call_args.kwargs["json"]["state"]
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
			self.assertEqual(detail.reload().as_dict(), original)

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
		set_value = frappe.db.set_value
		def fail_second(doctype, name, *args, **kwargs):
			if doctype == flow.DETAIL and name == self.details[1].name:
				raise RuntimeError("Second page write failed")
			return set_value(doctype, name, *args, **kwargs)
		with patch.object(frappe.db, "set_value", side_effect=fail_second), patch.object(frappe, "log_error"):
			flow.categorize_scan(self.scan.name)
		self.assertEqual(self.scan.reload().ai_check_status, "Failed")
		self.assertEqual(self.details[0].reload().attachment, original)
		self.assertFalse(self.details[0].original_attachment)

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
		for pages in ([], [{"page": 1, "summary": "Invoice", "rotation": 45}],
			[{"page": 1, "summary": "Invoice", "rotation": 0}] * 2):
			self.vision_mock.return_value = {"data": {"pages": pages}}
			with self.assertRaises(ValueError):
				summarize(["image"], target_doctype="Purchase Invoice")

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

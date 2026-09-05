from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from frappe_tools.extractors import pipeline


class TestExtractionBackgroundJobs(FrappeTestCase):
	def test_verified_high_confidence_values_are_auto_approved(self):
		box = '{"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.05, "source": "ocr", "verified": true}'
		field = SimpleNamespace(status="Pending", value="INV-1", confidence=0.96, bbox_json=box)
		line = SimpleNamespace(resolution_status="Matched", match_confidence=0.95, bbox_json=box)
		pipeline.apply_review_decisions(SimpleNamespace(extracted_fields=[field], lines=[line]))
		self.assertEqual(field.status, "Approved")
		self.assertEqual(line.resolution_status, "Confirmed")

	def test_low_confidence_or_unverified_values_require_handoff(self):
		model_box = '{"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.05, "source": "model", "verified": false}'
		field = SimpleNamespace(status="Pending", value="INV-1", confidence=0.99, bbox_json=model_box)
		line = SimpleNamespace(resolution_status="Matched", match_confidence=0.70, bbox_json=model_box)
		pipeline.apply_review_decisions(SimpleNamespace(extracted_fields=[field], lines=[line]))
		self.assertEqual(field.status, "Pending")
		self.assertEqual(line.resolution_status, "Matched")

	@patch("frappe_tools.extractors.pipeline.S.build_header_schema")
	def test_per_field_policy_can_disable_auto_approval(self, schema):
		schema.return_value = [{"fieldname": "account", "minimum_confidence": 0.9, "auto_approve": False}]
		box = '{"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.05, "source": "ocr", "verified": true}'
		field = SimpleNamespace(fieldname="account", status="Pending", value="Expenses", confidence=0.99, bbox_json=box)
		doc = SimpleNamespace(target_doctype="Purchase Invoice", extracted_fields=[field], lines=[])
		pipeline.apply_review_decisions(doc)
		self.assertEqual(field.status, "Pending")

	@patch("frappe_tools.extractors.pipeline.S.build_header_schema")
	def test_per_field_threshold_is_respected(self, schema):
		schema.return_value = [{"fieldname": "bill_no", "minimum_confidence": 0.97, "auto_approve": True}]
		box = '{"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.05, "source": "ocr", "verified": true}'
		field = SimpleNamespace(fieldname="bill_no", status="Pending", value="A-1", confidence=0.95, bbox_json=box)
		doc = SimpleNamespace(target_doctype="Purchase Invoice", extracted_fields=[field], lines=[])
		pipeline.apply_review_decisions(doc)
		self.assertEqual(field.status, "Pending")

	def test_direct_build_gate_rejects_unreviewed_data(self):
		doc = SimpleNamespace(
			extracted_fields=[SimpleNamespace(value="INV-1", status="Pending", label="Invoice No", fieldname="invoice_no")],
			lines=[SimpleNamespace(row_no=1, resolution_status="Unmatched")],
		)
		issues = pipeline.validate_review_acceptance(doc)
		self.assertEqual(len(issues), 2)
		self.assertIn("Invoice No", issues[0])

	def test_direct_build_gate_allows_accepted_or_rejected_data(self):
		doc = SimpleNamespace(
			extracted_fields=[
				SimpleNamespace(value="INV-1", status="Approved", label="Invoice No", fieldname="invoice_no"),
				SimpleNamespace(value="guess", status="Rejected", label="Remarks", fieldname="remarks"),
			],
			lines=[SimpleNamespace(row_no=1, resolution_status="Confirmed")],
		)
		self.assertEqual(pipeline.validate_review_acceptance(doc), [])

	def test_timeout_is_three_minutes_per_page_by_default(self):
		settings = {"timeout_per_page_minutes": 3, "background_queue": "long"}
		self.assertEqual(pipeline.processing_timeout_seconds(1, settings), 180)
		self.assertEqual(pipeline.processing_timeout_seconds(4, settings), 720)

	def test_timeout_uses_configured_minutes_per_page(self):
		settings = {"timeout_per_page_minutes": 5, "background_queue": "default"}
		self.assertEqual(pipeline.processing_timeout_seconds(3, settings), 900)

	def test_invalid_queue_falls_back_to_long(self):
		config = pipeline.processing_config({"background_queue": "unknown", "timeout_per_page_minutes": 3})
		self.assertEqual(config["queue"], "long")

	@patch("frappe_tools.extractors.pipeline.frappe.enqueue")
	@patch("frappe_tools.extractors.pipeline.processing_config")
	def test_each_extraction_gets_its_own_deduplicated_job(self, processing_config, enqueue):
		processing_config.return_value = {
			"queue": "long", "timeout_per_page_minutes": 3, "require_verified_evidence": True,
		}
		first = pipeline.enqueue_extraction("DOCEXT-00001", 2, enqueue_after_commit=True)
		second = pipeline.enqueue_extraction("DOCEXT-00002", 5, enqueue_after_commit=True)

		self.assertEqual(first["timeout_seconds"], 360)
		self.assertEqual(second["timeout_seconds"], 900)
		self.assertNotEqual(first["job_id"], second["job_id"])
		self.assertEqual(enqueue.call_count, 2)
		self.assertEqual(enqueue.call_args_list[0].kwargs["job_id"], "document-extraction::DOCEXT-00001")
		self.assertTrue(enqueue.call_args_list[0].kwargs["deduplicate"])
		self.assertEqual(enqueue.call_args_list[1].kwargs["timeout"], 900)

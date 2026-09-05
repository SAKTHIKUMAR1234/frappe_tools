from types import SimpleNamespace
from unittest.mock import Mock, patch

from frappe.tests.utils import FrappeTestCase

from frappe_tools.extractors import classification


class TestDocumentClassification(FrappeTestCase):
	def test_supported_high_confidence_target_is_accepted(self):
		result = classification._normalize_decision(
			{
				"target_doctype": "LR Processing",
				"confidence": 0.94,
				"document_category": "Delhivery",
				"reasons": ["lorry receipt heading"],
				"handoff_reasons": [],
			},
			[{"target_doctype": "LR Processing"}],
		)

		self.assertTrue(result["accepted"])
		self.assertEqual(result["target_doctype"], "LR Processing")

	def test_low_confidence_target_requires_handoff(self):
		result = classification._normalize_decision(
			{"target_doctype": "Purchase Invoice", "confidence": 0.61},
			[{"target_doctype": "Purchase Invoice"}],
		)

		self.assertFalse(result["accepted"])
		self.assertIn("classification_confidence_below_0.85", result["handoff_reasons"])

	def test_unsupported_target_is_never_executed(self):
		result = classification._normalize_decision(
			{"target_doctype": "Sales Invoice", "confidence": 1},
			[{"target_doctype": "Purchase Invoice"}],
		)

		self.assertFalse(result["accepted"])
		self.assertIsNone(result["target_doctype"])

	@patch("frappe_tools.extractors.classification.frappe.enqueue")
	def test_classification_job_is_deduplicated(self, enqueue):
		doc = SimpleNamespace(
			doctype="Document Extraction",
			name="DOCEXT-AUTO",
			target_doctype=None,
			pages=[SimpleNamespace(image="/private/files/page.png")],
			status="Draft",
			classification_phase=None,
			classification_job_id=None,
			handoff_reason=None,
			error_log=None,
			flags=SimpleNamespace(skip_auto_process=False),
			save=Mock(),
			add_processing_event=Mock(),
		)

		result = classification.enqueue(doc, enqueue_after_commit=True)

		self.assertEqual(result["status"], "Classifying")
		self.assertEqual(doc.classification_phase, "Queued")
		self.assertEqual(enqueue.call_args.kwargs["job_id"], "document-classification::DOCEXT-AUTO")
		self.assertTrue(enqueue.call_args.kwargs["deduplicate"])

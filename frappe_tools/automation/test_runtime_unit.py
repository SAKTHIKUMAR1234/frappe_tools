from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from frappe_tools.automation import runtime


class TestDecisionReprocessUnit(TestCase):
	@patch("frappe_tools.utils.llm.call_vision")
	@patch("frappe_tools.automation.runtime.enqueue_decision", return_value={"queued": True})
	@patch("frappe_tools.automation.runtime.prepare", return_value={"adapter_id": "ERPNext:Purchase Invoice"})
	@patch("frappe_tools.automation.runtime.frappe.get_doc")
	def test_reprocess_has_no_vision_path(self, get_doc, prepare, enqueue, call_vision):
		extraction = SimpleNamespace(
			name="DOCEXT-1",
			status="Review",
			reference_bundle_json="old",
			decision_json="old",
			tool_trace_json="old",
			handoff_reason="missing HSN",
			decision_phase="Human Handoff",
			flags=SimpleNamespace(),
			save=Mock(),
			add_processing_event=Mock(),
		)
		get_doc.return_value = extraction

		result = runtime.reprocess("DOCEXT-1")

		self.assertTrue(result["queued"])
		prepare.assert_called_once_with("DOCEXT-1")
		enqueue.assert_called_once_with("DOCEXT-1", enqueue_after_commit=True, force=True)
		call_vision.assert_not_called()
		self.assertIsNone(extraction.decision_json)

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from frappe_tools import scan_lineage


class TestScanLineage(TestCase):
	@patch("frappe_tools.scan_lineage.now_datetime", return_value="2026-09-04 12:00:00")
	@patch("frappe_tools.scan_lineage.link_extraction_to_target", return_value="DOC_SCAN-00001")
	def test_finalize_extraction_target_persists_one_shared_completion(self, link, _now):
		doc = SimpleNamespace(
			name="DOCEXT-TEST",
			target_doctype="ToDo",
			created_document=None,
			scanned_document=None,
			status="Review",
			processing_completed_on=None,
			flags=SimpleNamespace(skip_auto_process=False),
			add_processing_event=Mock(),
			save=Mock(),
		)

		result = scan_lineage.finalize_extraction_target(doc, "TODO-00001")

		link.assert_called_once_with(doc, "TODO-00001")
		self.assertEqual(doc.created_document, "TODO-00001")
		self.assertEqual(doc.scanned_document, "DOC_SCAN-00001")
		self.assertEqual(doc.status, "Created")
		doc.save.assert_called_once_with(ignore_permissions=True)
		self.assertEqual(result["scanned_document"], "DOC_SCAN-00001")

	def test_layout_is_required_only_for_scanned_document_materialization(self):
		doc = SimpleNamespace(
			target_doctype="ToDo",
			source_files=[],
			pages=[],
			get=lambda key: None,
		)

		frappe_api = Mock()
		frappe_api.db.exists.return_value = True
		with patch.object(scan_lineage, "frappe", frappe_api):
			self.assertIsNone(scan_lineage.link_extraction_to_target(doc, "TODO-00001"))

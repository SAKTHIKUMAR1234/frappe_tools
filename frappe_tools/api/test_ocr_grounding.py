import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from frappe_tools.api import ocr_grounding


class TestCellEvidence(FrappeTestCase):
	def make_doc(self):
		row = SimpleNamespace(table="items", row_no=1, source_page=1,
			bbox_json='{"x":0.1,"y":0.2,"w":0.8,"h":0.1}',
			raw_json=json.dumps({"qty": 2, "_cell_evidence": {"amount": {"source": "ocr", "page": 1, "x": 0.8}}}))
		return SimpleNamespace(name="DOC-TEST", status="Review", target_doctype="Purchase Invoice", line_table="items",
			lines=[row], pages=[SimpleNamespace(page_no=2, width=1000, height=1400)], save=Mock())

	@patch("frappe_tools.api.ocr_grounding.get_run", return_value={})
	@patch("frappe_tools.api.ocr_grounding.get_plugin")
	@patch("frappe_tools.api.ocr_grounding._get_extraction")
	def test_correcting_one_cell_preserves_other_cells_and_row_box(self, get_extraction, get_plugin, _run):
		doc = self.make_doc()
		get_extraction.return_value = doc
		get_plugin.return_value.schema.return_value = {"tables": [{"table": "items", "columns": [{"key": "qty"}]}]}
		before = doc.lines[0].bbox_json
		ocr_grounding.update_bbox("DOC-TEST", "table", 2, {"x": 0.2, "y": 0.3, "w": 0.1, "h": 0.05}, table="items", row_no=1, column="qty")
		data = json.loads(doc.lines[0].raw_json)
		self.assertEqual(data["qty"], 2)
		self.assertEqual(data["_cell_evidence"]["qty"]["page"], 2)
		self.assertEqual(data["_cell_evidence"]["qty"]["source"], "manual")
		self.assertEqual(data["_cell_evidence"]["amount"]["x"], 0.8)
		self.assertEqual(doc.lines[0].bbox_json, before)
		self.assertEqual(doc.lines[0].source_page, 1)
		doc.save.assert_called_once()

	@patch("frappe_tools.api.ocr_grounding.get_plugin")
	@patch("frappe_tools.api.ocr_grounding._get_extraction")
	def test_unknown_column_is_rejected_without_saving(self, get_extraction, get_plugin):
		doc = self.make_doc()
		get_extraction.return_value = doc
		get_plugin.return_value.schema.return_value = {"tables": [{"table": "items", "columns": [{"key": "qty"}]}]}
		with self.assertRaises(frappe.ValidationError):
			ocr_grounding.update_bbox("DOC-TEST", "table", 2, {"x": 0.2, "y": 0.3, "w": 0.1, "h": 0.05}, table="items", row_no=1, column="unknown")
		doc.save.assert_not_called()

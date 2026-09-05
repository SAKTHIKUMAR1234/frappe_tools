import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from frappe_tools.api import doc_resolve


class TestDocumentResolutionApi(FrappeTestCase):
	@patch("frappe_tools.automation.learning.now_datetime", return_value="2026-09-03 12:00:00")
	@patch("frappe_tools.api.doc_resolve.get_plugin")
	@patch("frappe_tools.api.doc_resolve.frappe.get_doc")
	def test_confirm_line_item_records_agent_human_delta(self, get_doc, get_plugin, _now):
		line = SimpleNamespace(row_no=1, table="items", matched_item="ITEM-AGENT")
		doc = frappe._dict(
			target_doctype="Purchase Invoice", status="Review", line_table="items", lines=[line],
			decision_json='{"values":{"items":[{"row_no":1,"item_code":"ITEM-AGENT"}]}}',
			review_delta_json=None, check_permission=Mock(),
		)
		plugin = get_plugin.return_value
		plugin.schema.return_value = {"tables": [{"table": "items", "resolver": {"value_key": "item_code"}}]}
		plugin.confirm_row.return_value = {"ok": True, "value": "ITEM-HUMAN"}
		get_doc.return_value = doc

		result = doc_resolve.confirm_line_item("DOCEXT-1", 1, "ITEM-HUMAN")

		delta = json.loads(doc.review_delta_json)[0]
		self.assertEqual(delta["path"], "table:items:1:item_code")
		self.assertEqual(delta["agent_value"], "ITEM-AGENT")
		self.assertEqual(delta["human_value"], "ITEM-HUMAN")
		self.assertTrue(result["ok"])

	@patch("frappe_tools.api.doc_resolve.frappe.get_doc")
	def test_resolution_is_blocked_outside_review(self, get_doc):
		get_doc.return_value = SimpleNamespace(status="Created", check_permission=Mock())

		with self.assertRaises(frappe.ValidationError):
			doc_resolve.confirm_line_item("DOCEXT-1", 1, "ITEM-1")

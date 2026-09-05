from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from frappe_tools.api import adapter_ui


class TestAdapterUiBoundary(TestCase):
	def setUp(self):
		self.doc = frappe._dict(name="REVIEW", target_doctype="Purchase Invoice", status="Review",
			modified="2026-09-05 12:00:00", decision_phase="Human Handoff")
		self.doc.save = Mock()
		self.doc.add_processing_event = Mock()
		self.handler = Mock(return_value={"ok": True})
		self.plugin = Mock()
		self.plugin.review_actions.return_value = {"create_item": {"handler": self.handler, "permissions": [("Item", "create")]}}
		self.patches = [patch.object(adapter_ui, "_get_extraction", return_value=self.doc),
			patch.object(adapter_ui, "get_plugin", return_value=self.plugin),
			patch.object(adapter_ui, "frappe", wraps=frappe)]
		for mocked in self.patches:
			mocked.start()
			self.addCleanup(mocked.stop)
		adapter_ui.frappe.has_permission = Mock(return_value=True)
		adapter_ui.frappe.session = SimpleNamespace(user="reviewer@example.test")
		adapter_ui.frappe.throw = Mock(side_effect=lambda message, exc=frappe.ValidationError: (_ for _ in ()).throw(exc(message)))

	def test_arbitrary_python_path_cannot_be_called(self):
		with self.assertRaises(frappe.PermissionError):
			adapter_ui.run_action("REVIEW", "frappe.client.insert", {}, modified=self.doc.modified)
		self.handler.assert_not_called()

	def test_stale_review_cannot_create_a_master(self):
		with self.assertRaises(frappe.TimestampMismatchError):
			adapter_ui.run_action("REVIEW", "create_item", {}, modified="earlier")
		self.handler.assert_not_called()

	def test_permission_denial_precedes_handler(self):
		adapter_ui.frappe.has_permission.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			adapter_ui.run_action("REVIEW", "create_item", {}, modified=self.doc.modified)
		self.handler.assert_not_called()

	def test_processing_and_completed_documents_reject_actions(self):
		for status, phase in (("Created", "Applied to Review"), ("Review", "Running"), ("Review", "Queued")):
			self.doc.status, self.doc.decision_phase = status, phase
			with self.subTest(status=status, phase=phase), self.assertRaises(frappe.ValidationError):
				adapter_ui.run_action("REVIEW", "create_item", {}, modified=self.doc.modified)
		self.handler.assert_not_called()

	def test_action_receives_only_its_values_and_records_reviewer(self):
		self.assertEqual(adapter_ui.run_action("REVIEW", "create_item", {"row_no": 3}, modified=self.doc.modified), {"ok": True})
		self.assertEqual(self.handler.call_args.args[2], {"row_no": 3})
		self.assertEqual(self.doc.add_processing_event.call_args.args[3]["user"], "reviewer@example.test")

	@patch.object(adapter_ui, "get_permitted_fields", return_value=["name", "city", "secret"])
	def test_preview_retains_supplier_scope_and_hides_restricted_fields(self, _permitted):
		self.plugin.link_queries.return_value = {"address": {"doctype": "Address",
			"fields": ["city", "private_note", "secret"], "filters": {"name": ["in", ["ALLOWED"]]}}}
		meta = Mock()
		meta.has_field.return_value = True
		meta.get_title_field.return_value = "name"
		meta.get_field.side_effect = lambda name: SimpleNamespace(label=name, fieldtype="Password" if name == "secret" else "Data")
		adapter_ui.frappe.get_meta = Mock(return_value=meta)
		adapter_ui.frappe.get_list = Mock(return_value=[frappe._dict(name="ALLOWED", city="Salem")])
		result = adapter_ui.search_link("REVIEW", "address", value="OTHER")
		query = adapter_ui.frappe.get_list.call_args.kwargs
		self.assertEqual(query["fields"], ["name", "city"])
		self.assertIn(["name", "in", ["ALLOWED"]], query["filters"])
		self.assertIn(["name", "=", "OTHER"], query["filters"])
		self.assertEqual(result[0]["details"], [{"label": "city", "value": "Salem"}])

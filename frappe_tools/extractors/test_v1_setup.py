from unittest import TestCase
from unittest.mock import Mock, patch

from frappe_tools.extractors import v1_setup


class TestDocumentAutomationSetup(TestCase):
	def test_existing_profiles_are_not_rewritten_on_migration(self):
		frappe = Mock()
		frappe.db.exists.return_value = True
		frappe.get_all.return_value = []
		frappe.db.get_value.side_effect = lambda doctype, filters: filters["title"]
		with patch.object(v1_setup, "frappe", frappe):
			self.assertEqual(v1_setup.install_profiles(), ["V1 - LR Processing", "V1 - Purchase Invoice"])
		frappe.get_doc.assert_not_called()
		frappe.new_doc.assert_not_called()
		frappe.get_meta.assert_not_called()

	def test_missing_profiles_are_still_seeded(self):
		frappe = Mock()
		frappe.db.exists.side_effect = lambda doctype, name: name == "Purchase Invoice"
		frappe.db.get_value.return_value = None
		doc = Mock()
		doc.name = "V1 - Purchase Invoice"
		frappe.new_doc.return_value = doc
		frappe.get_meta.return_value.get_field.return_value = Mock(label="Value", reqd=0, fieldtype="Data")
		with patch.object(v1_setup, "frappe", frappe):
			self.assertEqual(v1_setup.install_profiles(), ["V1 - Purchase Invoice"])
		frappe.new_doc.assert_called_once_with("Document Rule Book")
		doc.save.assert_called_once_with(ignore_permissions=True)

	def test_lr_layout_is_seeded_when_adapter_has_no_layout(self):
		frappe = Mock()
		frappe.db.exists.side_effect = lambda doctype, filters=None: (
			True if doctype in {"DocType", "Document Layout Section Title"} else False
		)
		layout = Mock(name="LR Documents")
		layout.name = "LR Documents"
		frappe.get_doc.return_value = layout
		plugin = Mock()
		plugin.scanner_layouts.return_value = [{
			"layout": "LR Documents",
			"target_doctype": "LR Processing",
			"sections": [
				{"title": "LR Copy", "layout_type": "Single Page"},
				{"title": "Others", "layout_type": "Series Vertical"},
			],
		}]

		with patch.object(v1_setup, "frappe", frappe), \
			patch("frappe_tools.extractors.registered_targets", return_value=["LR Processing"]), \
			patch("frappe_tools.extractors.get_plugin", return_value=plugin):
			result = v1_setup.ensure_default_layouts()

		self.assertEqual(result, ["LR Documents"])
		spec = frappe.get_doc.call_args.args[0]
		self.assertEqual(spec["layout_doctype"], "LR Processing")
		self.assertEqual([row["title"] for row in spec["layout_doctype_sections"]], ["LR Copy", "Others"])
		layout.insert.assert_called_once_with(ignore_permissions=True)
		layout.submit.assert_called_once()

	def test_existing_lr_layout_is_not_replaced(self):
		frappe = Mock()
		frappe.db.exists.return_value = True
		plugin = Mock()
		with patch.object(v1_setup, "frappe", frappe), \
			patch("frappe_tools.extractors.registered_targets", return_value=["LR Processing"]), \
			patch("frappe_tools.extractors.get_plugin", return_value=plugin):
			self.assertEqual(v1_setup.ensure_default_layouts(), [])
		plugin.scanner_layouts.assert_not_called()

	def test_missing_others_section_is_added_by_core(self):
		sections = v1_setup._layout_sections({
			"sections": [{"title": "Invoice", "layout_type": "Single Page"}],
		})
		self.assertEqual([row["title"] for row in sections], ["Invoice", "Others"])

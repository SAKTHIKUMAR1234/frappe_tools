from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from frappe_tools import extractors
from frappe_tools.extractors.workflow import manifest


class TestAdapterPackages(TestCase):
	def test_hook_registry_is_isolated_between_sites(self):
		first = type("First", (), {"__module__": "first.adapter.plugin", "system": "ERPNext", "target_doctype": "One"})
		second = type("Second", (), {"__module__": "second.adapter.plugin", "system": "ERPNext", "target_doctype": "Two"})
		with patch.object(extractors, "_DECLARED", {"first": first, "second": second}), \
			patch.object(extractors, "_REGISTRY", {}), patch.object(extractors, "_LOADED", False), \
			patch.object(extractors, "_HOOKS", None), patch.object(extractors.importlib, "import_module"), \
			patch.object(extractors.frappe, "get_hooks", side_effect=[["first.adapter"], ["second.adapter.plugin"], ["first.adapter"]]):
			self.assertEqual(extractors.registered_targets(), ["One"])
			self.assertEqual(extractors.registered_targets(), ["Two"])
			self.assertEqual(extractors.registered_targets(), ["One"])

	def test_custom_handoff_section_keeps_other_extracted_fields_available(self):
		plugin = SimpleNamespace(target_doctype="Dispatch", adapter_id=lambda: "dispatch", workflow=lambda ctx: {
			"review_sections": [{"key": "allocation", "label": "Choose destination", "custom": True}]})
		result = manifest(plugin, None, {"header": [{"fieldname": "reference"}]})
		self.assertEqual(result["review_sections"][0]["kind"], "custom")
		self.assertEqual(result["review_sections"][1]["fields"], ["reference"])

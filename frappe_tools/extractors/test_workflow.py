from types import SimpleNamespace
from unittest import TestCase

from frappe_tools.extractors.workflow import manifest


class TestWorkflowManifest(TestCase):
	def plugin(self, config):
		return SimpleNamespace(target_doctype="Dispatch Note", adapter_id=lambda: "example.dispatch", workflow=lambda ctx: config)

	def test_future_adapter_keeps_omitted_fields_and_tables(self):
		schema = {"header": [{"fieldname": "destination"}, {"fieldname": "required_extra"}], "tables": [{"table": "styles", "label": "Style codes"}]}
		result = manifest(self.plugin({"review_sections": [{"key": "dispatch", "fields": ["destination"]}]}), None, schema)
		self.assertEqual([section["key"] for section in result["review_sections"]], ["dispatch", "details", "table-styles"])
		self.assertEqual(result["review_sections"][1]["fields"], ["required_extra"])

	def test_unknown_duplicate_and_reserved_sections_fail_closed(self):
		for sections in ([{"key": "checks", "fields": ["name"]}], [{"key": "a", "fields": ["missing"]}], [{"key": "a", "fields": ["name", "name"]}]):
			with self.subTest(sections=sections), self.assertRaises(ValueError):
				manifest(self.plugin({"review_sections": sections}), None, {"header": [{"fieldname": "name"}]})

	def test_callable_is_not_exposed_and_icon_is_sanitized(self):
		result = manifest(self.plugin({"icon": "<script>", "execute": lambda: None, "action_label": "Save reviewed dispatch"}), None, {})
		self.assertEqual(result["icon"], "pi pi-file")
		self.assertEqual(result["action_label"], "Save reviewed dispatch")
		self.assertNotIn("execute", result)

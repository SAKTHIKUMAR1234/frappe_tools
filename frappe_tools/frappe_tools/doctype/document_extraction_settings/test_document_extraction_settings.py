from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from frappe_tools.file_data import extraction
from frappe_tools.frappe_tools.doctype.document_extraction_settings import (
	document_extraction_settings as settings,
)
from frappe_tools.scan_categorization import decisions
from frappe_tools.utils import llm


def blank_page() -> dict:
	page = dict.fromkeys(extraction.PAGE_SCHEMA)
	page.update(page_no=1, rotation=0, is_blank_or_unreadable=True)
	return page


class TestAIPrompts(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(frappe.db.rollback)
		self.enterContext(patch.object(frappe.db, "commit"))
		self.set_prompts(dict.fromkeys(settings.PROMPT_FIELDS.values(), None))

	def set_prompts(self, values):
		frappe.db.set_single_value(settings.SETTINGS_DOCTYPE, values)

	def test_blank_prompt_uses_default(self):
		for task, field in settings.PROMPT_FIELDS.items():
			self.assertEqual(settings.get_prompt(task), settings.DEFAULT_PROMPTS[task])
			self.set_prompts({field: "  \n"})
			self.assertEqual(settings.get_prompt(task), settings.DEFAULT_PROMPTS[task])
			self.set_prompts({field: "Custom prompt"})
			self.assertEqual(settings.get_prompt(task), "Custom prompt")

	def test_custom_page_extraction_prompt_keeps_schema(self):
		self.set_prompts({"page_extraction_prompt": "Read only the totals."})
		with patch.object(llm, "call_vision", return_value={"data": {"pages": [blank_page()]}}) as vision:
			self.assertEqual(extraction.extract_pages(["one"])[0], [blank_page()])

		system_prompt, user_prompt = vision.call_args.args[1:3]
		self.assertEqual(system_prompt, f"Read only the totals.\n\n{extraction.PAGE_SAFETY_RULES}")
		for field in extraction.PAGE_SCHEMA:
			self.assertIn(field, user_prompt)

		for data in ({"summary": "Totals only"}, {"pages": [{"page_no": 1, "rotation": 0, "totals": {}}]}):
			with (
				self.subTest(data=data), patch.object(llm, "call_vision", return_value={"data": data}),
				self.assertRaises(ValueError),
			):
				extraction.extract_pages(["one"])

	def test_custom_scan_decision_prompt_keeps_rules(self):
		self.set_prompts({"scan_decision_prompt": "Prefer the Bill layout."})
		answer = {"layout": "Bill", "confidence": 0.9, "reason": "Invoice"}
		layouts = {"Bill": [{"title": "Invoice", "layout_type": "Single Page"}]}
		with patch.object(decisions, "call_model", return_value={"data": answer}) as model:
			self.assertEqual(decisions.decide_layout({}, layouts, "Purchase Invoice"), answer)

		self.assertEqual(model.call_args.args[1], f"Prefer the Bill layout.\n\n{decisions.DECISION_RULES}")
		self.assertEqual(model.call_args.kwargs["schema"]["required"], ["layout", "confidence", "reason"])

	def test_load_default_prompts_fills_only_blanks(self):
		self.set_prompts({"scan_decision_prompt": "Custom decision prompt"})
		self.assertEqual(settings.load_default_prompts(), ["page_extraction_prompt", "verifier_system_prompt"])

		doc = frappe.get_single(settings.SETTINGS_DOCTYPE)
		self.assertEqual(doc.page_extraction_prompt, settings.DEFAULT_PROMPTS["Page Extraction"])
		self.assertEqual(doc.scan_decision_prompt, "Custom decision prompt")
		self.assertEqual(doc.verifier_system_prompt, settings.DEFAULT_PROMPTS["Verifier"])
		self.assertEqual(settings.load_default_prompts(), [])

	def test_load_default_prompts_needs_system_manager_and_post(self):
		self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[settings.load_default_prompts], ["POST"])
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			settings.load_default_prompts()

		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.get_single_value(settings.SETTINGS_DOCTYPE, "page_extraction_prompt", cache=False))

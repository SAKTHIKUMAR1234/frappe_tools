from types import SimpleNamespace
from unittest import TestCase

import frappe

from frappe_tools.extractors import layout_routing


PROFILES = [{
	"layout": "Yarn Bill",
	"target_doctype": "Purchase Invoice",
	"sections": [
		{"title": "Tax Invoice", "layout_type": "Front And Back Vertical", "order": 1},
		{"title": "Purchase Order", "layout_type": "Series Vertical", "order": 2},
		{"title": "Company GRN", "layout_type": "Series Vertical", "order": 3},
		{"title": "Others", "layout_type": "Series Vertical", "order": 4},
	],
}]


class _Document(SimpleNamespace):
	def set(self, fieldname, value):
		setattr(self, fieldname, value)


class TestLayoutRouting(TestCase):
	def test_shuffled_pages_are_ordered_by_layout_then_document_sequence(self):
		decision = layout_routing._normalize_decision({
			"layout": "Yarn Bill",
			"confidence": 0.97,
			"reasons": ["yarn invoice package"],
			"pages": [
				{"input_page": 1, "section": "Company GRN", "page_type": "Front", "document_sequence": 1, "page_sequence": 1, "confidence": 0.96},
				{"input_page": 2, "section": "Tax Invoice", "page_type": "Back", "document_sequence": 1, "page_sequence": 2, "confidence": 0.95},
				{"input_page": 3, "section": "Tax Invoice", "page_type": "Front", "document_sequence": 1, "page_sequence": 1, "confidence": 0.98},
				{"input_page": 4, "section": "Purchase Order", "page_type": "Front", "document_sequence": 1, "page_sequence": 1, "confidence": 0.94},
			],
		}, PROFILES, 4)

		self.assertTrue(decision["accepted"])
		doc = _Document(pages=[
			frappe._dict(page_no=1, image="grn.png"),
			frappe._dict(page_no=2, image="invoice-back.png"),
			frappe._dict(page_no=3, image="invoice-front.png"),
			frappe._dict(page_no=4, image="po.png"),
		])
		layout_routing._apply_page_plan(doc, decision, PROFILES[0])

		self.assertEqual([row.image for row in doc.pages], [
			"invoice-front.png", "invoice-back.png", "po.png", "grn.png",
		])
		self.assertEqual([row.page_no for row in doc.pages], [1, 2, 3, 4])
		self.assertEqual([row.original_page_no for row in doc.pages], [3, 2, 4, 1])
		self.assertEqual([row.layout_section for row in doc.pages], [
			"Tax Invoice", "Tax Invoice", "Purchase Order", "Company GRN",
		])

	def test_unrecognized_page_is_preserved_in_others(self):
		decision = layout_routing._normalize_decision({
			"layout": "Yarn Bill",
			"confidence": 0.99,
			"pages": [
				{"input_page": 1, "section": "Tax Invoice", "confidence": 0.98},
				{"input_page": 2, "section": "Unknown Attachment", "confidence": 0.99},
			],
		}, PROFILES, 2)

		self.assertTrue(decision["accepted"])
		self.assertEqual(decision["pages"][1]["section"], "Others")
		self.assertTrue(decision["pages"][1]["fallback"])

	def test_missing_model_page_is_preserved_in_others(self):
		decision = layout_routing._normalize_decision({
			"layout": "Yarn Bill",
			"confidence": 0.99,
			"pages": [{"input_page": 1, "section": "Tax Invoice", "confidence": 0.98}],
		}, PROFILES, 2, required_layout="Yarn Bill")

		self.assertTrue(decision["accepted"])
		self.assertEqual(decision["pages"][1]["input_page"], 2)
		self.assertEqual(decision["pages"][1]["section"], "Others")

	def test_low_layout_confidence_never_reorders_pages(self):
		decision = layout_routing._normalize_decision({
			"layout": "Yarn Bill",
			"confidence": 0.62,
			"pages": [{"input_page": 1, "section": "Tax Invoice", "confidence": 0.99}],
		}, PROFILES, 1)

		self.assertFalse(decision["accepted"])
		self.assertIn("layout_confidence_below_0.85", decision["handoff_reasons"])

	def test_user_selected_layout_is_not_overridden_by_model_confidence(self):
		decision = layout_routing._normalize_decision({
			"layout": "A different invented layout",
			"confidence": 0.62,
			"pages": [{"input_page": 1, "section": "Tax Invoice", "confidence": 0.99}],
		}, PROFILES, 1, required_layout="Yarn Bill")

		self.assertTrue(decision["accepted"])
		self.assertEqual(decision["layout"], "Yarn Bill")
		self.assertNotIn("layout_confidence_below_0.85", decision["handoff_reasons"])

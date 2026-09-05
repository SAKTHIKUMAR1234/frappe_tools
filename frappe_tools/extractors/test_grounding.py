import json
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from frappe_tools.extractors import grounding
from frappe_tools.extractors import pipeline
from frappe_tools.i2a import extract, ground


class TestExtractionGrounding(FrappeTestCase):
	def test_locate_uses_claimed_region_to_disambiguate_duplicate_text(self):
		pages = {1: {"words": []}}
		matches = [
			{"bbox": {"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.03}, "text": "100", "score": 1},
			{"bbox": {"x": 0.7, "y": 0.8, "w": 0.1, "h": 0.03}, "text": "100", "score": 1},
		]
		with patch("frappe_tools.extractors.grounding.ground.match_value", return_value=matches):
			result = grounding.locate(["100"], pages, 1, {"x": 0.68, "y": 0.78, "w": 0.2, "h": 0.1})
		self.assertEqual(result["bbox"]["x"], 0.7)

	def test_locate_rejects_ambiguous_matches_without_claimed_region(self):
		pages = {1: {"words": []}}
		matches = [
			{"bbox": {"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.03}, "text": "100", "score": 1},
			{"bbox": {"x": 0.7, "y": 0.8, "w": 0.1, "h": 0.03}, "text": "100", "score": 1},
		]
		with patch("frappe_tools.extractors.grounding.ground.match_value", return_value=matches):
			self.assertIsNone(grounding.locate(["100"], pages))

	def test_short_numeric_match_cannot_jump_to_a_distant_duplicate(self):
		pages = {1: {"words": []}}
		match = [{"bbox": {"x": 0.61, "y": 0.62, "w": 0.03, "h": 0.03}, "text": "9%", "score": 1}]
		with patch("frappe_tools.extractors.grounding.ground.match_value", return_value=match):
			self.assertIsNone(
				grounding.locate(["9"], pages, 1, {"x": 0.47, "y": 0.35, "w": 0.02, "h": 0.04})
			)

	def test_locate_never_moves_evidence_to_another_claimed_page(self):
		pages = {1: {"words": []}, 2: {"words": []}}
		def matches(_targets, words):
			if words is pages[2]["words"]:
				return [{
					"bbox": {"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.03},
					"text": "Invoice 12", "score": 1,
				}]
			return []
		with patch("frappe_tools.extractors.grounding.ground.match_value", side_effect=matches):
			self.assertIsNone(
				grounding.locate(["Invoice 12"], pages, 1, {"x": 0.5, "y": 0.5, "w": 0.2, "h": 0.1})
			)

	def test_apply_replaces_model_box_and_preserves_it_for_audit(self):
		field = SimpleNamespace(
			llm_raw_text="Invoice 12", llm_value="12", value="12", source_page=1,
			bbox_json=json.dumps({"x": 0.5, "y": 0.5, "w": 0.2, "h": 0.1}),
		)
		doc = SimpleNamespace(extracted_fields=[field], lines=[], pages=[SimpleNamespace(page_no=1, image="page")])
		match = {"bbox": {"x": 0.1, "y": 0.2, "w": 0.1, "h": 0.03}, "text": "12", "score": 1, "page": 1}
		with patch("frappe_tools.extractors.grounding._load_pages", return_value={1: {"words": []}}), patch(
			"frappe_tools.extractors.grounding.locate", return_value=match
		):
			stats = grounding.apply(doc)
		box = json.loads(field.bbox_json)
		self.assertEqual(box["source"], "ocr")
		self.assertTrue(box["verified"])
		self.assertEqual(box["x"], 0.1)
		self.assertEqual(box["model_bbox"]["x"], 0.5)
		self.assertEqual(stats["grounded"], 1)

	def test_manual_box_is_never_overwritten(self):
		field = SimpleNamespace(
			llm_raw_text="12", llm_value="12", value="12", source_page=1,
			bbox_json=json.dumps({"x": 0.2, "y": 0.3, "w": 0.1, "h": 0.04, "source": "manual"}),
		)
		doc = SimpleNamespace(extracted_fields=[field], lines=[], pages=[SimpleNamespace(page_no=1, image="page")])
		with patch("frappe_tools.extractors.grounding._load_pages", return_value={1: {"words": []}}):
			grounding.apply(doc)
		self.assertEqual(json.loads(field.bbox_json)["source"], "manual")

	def test_model_fallback_is_explicitly_unverified(self):
		box = grounding._resolved_box(
			None, {"x": 0.2, "y": 0.3, "w": 0.2, "h": 0.05}, 1,
		)
		self.assertEqual(box["source"], "model")
		self.assertFalse(box["verified"])

	def test_locate_row_unions_multiple_value_boxes(self):
		pages = {1: {"words": [], "width": 1000, "height": 1400}}
		matches = {
			"Cotton Yarn": {"bbox": {"x": 0.1, "y": 0.3, "w": 0.2, "h": 0.03}, "text": "Cotton Yarn", "score": 1, "page": 1},
			"18": {"bbox": {"x": 0.7, "y": 0.3, "w": 0.05, "h": 0.03}, "text": "18", "score": 1, "page": 1},
		}
		with patch("frappe_tools.extractors.grounding.locate", side_effect=lambda targets, *_args: matches.get(targets[0])):
			result = grounding.locate_row(["Cotton Yarn", "18"], pages, 1)
		self.assertEqual(result["bbox"]["x"], 0.1)
		self.assertGreater(result["bbox"]["w"], 0.6)

	def test_table_claims_preserve_per_cell_evidence_and_scalar_values(self):
		data = {"tables": {"entries": [{
			"extracted_lr_number": {"value": "46054", "raw_text": "GR No. 46054", "confidence": .96,
				"page": 1, "bbox": [100, 700, 150, 900]},
			"extracted_freight_amount": {"value": 1500, "raw_text": "1500.00", "confidence": .93,
				"page": 1, "bbox": [700, 800, 760, 940]},
		}]}}
		tables = [{"table": "entries", "columns": [
			{"key": "extracted_lr_number", "evidence": True},
			{"key": "extracted_freight_amount", "evidence": True},
		]}]
		row = pipeline.result_to_lines(data, tables)[0]
		raw = json.loads(row["raw_json"])
		self.assertEqual(raw["extracted_lr_number"], "46054")
		self.assertEqual(raw["extracted_freight_amount"], 1500)
		self.assertEqual(raw["_cell_evidence"]["extracted_lr_number"]["raw_text"], "GR No. 46054")
		self.assertAlmostEqual(raw["_cell_evidence"]["extracted_lr_number"]["bbox"]["x"], .7)

	def test_bbox_validation_rejects_non_finite_degenerate_and_subpixel_regions(self):
		self.assertIsNone(pipeline.normalize_bbox({"x": 0.1, "y": 0.2, "w": float("nan"), "h": 0.1}))
		self.assertIsNone(pipeline.normalize_bbox({"x": 0.1, "y": 0.2, "w": -0.1, "h": 0.1}))
		self.assertIsNone(pipeline.validate_bbox_for_page({"x": 0.1, "y": 0.2, "w": 0.002, "h": 0.01}, 1000, 100))

	@unittest.skipUnless(shutil.which("tesseract"), "tesseract is required for grounding integration")
	def test_printed_lr_invoice_and_grn_values_have_validated_boxes(self):
		cases = [
			("LR ENTRY", "LR No: 48217", "48217"),
			("PURCHASE INVOICE", "Bill No: PI-9011", "PI-9011"),
			("GOODS RECEIPT", "GRN No: GRN-4082", "GRN-4082"),
		]
		for title, line, target in cases:
			with self.subTest(operation=title):
				image, expected = _printed_page(title, line, target)
				words = ground.ocr_word_boxes(image)
				pages = {1: {"words": words, "width": image.width, "height": image.height}}
				result = grounding.locate([target], pages, 1)
				self.assertIsNotNone(result)
				self.assertGreater(extract.bbox_iou(result["bbox"], expected), 0.35)

	@unittest.skipUnless(shutil.which("tesseract"), "tesseract is required for grounding integration")
	def test_multi_page_value_stays_on_its_claimed_page(self):
		page_one, _ = _printed_page("PURCHASE INVOICE", "Bill No: PI-9011", "PI-9011")
		page_two, expected = _printed_page("PURCHASE INVOICE PAGE 2", "Grand Total: 111533.60", "111533.60")
		pages = {
			1: {"words": ground.ocr_word_boxes(page_one), "width": page_one.width, "height": page_one.height},
			2: {"words": ground.ocr_word_boxes(page_two), "width": page_two.width, "height": page_two.height},
		}
		result = grounding.locate(["111533.60"], pages, 2)
		self.assertEqual(result["page"], 2)
		self.assertGreater(extract.bbox_iou(result["bbox"], expected), 0.35)

	@unittest.skipUnless(shutil.which("tesseract"), "tesseract is required for grounding integration")
	def test_realistic_handwritten_packing_slip_values_are_grounded(self):
		fixture = Path(__file__).with_name("test_fixtures") / "handwritten_packing_slip.png"
		image = ground.load_image(fixture.read_bytes())
		words = ground.ocr_word_boxes(image)
		pages = {1: {"words": words, "width": image.width, "height": image.height}}
		for target in ("SIHMA TEXTILES", "Cotton Yarn 40s", "432", "Kumar"):
			with self.subTest(target=target):
				result = grounding.locate([target], pages, 1)
				self.assertIsNotNone(result)
				self.assertIsNotNone(grounding._usable_box(result["bbox"], image.width, image.height))


def _printed_page(title, line, target):
	from PIL import Image, ImageDraw, ImageFont

	image = Image.new("RGB", (1200, 1600), "white")
	draw = ImageDraw.Draw(image)
	font_candidates = (
		Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
		Path("/usr/share/fonts/google-noto-vf/NotoSans[wght].ttf"),
		Path("/usr/share/fonts/noto/NotoSans-Regular.ttf"),
	)
	font_path = next((str(path) for path in font_candidates if path.is_file()), "DejaVuSans.ttf")
	title_font = ImageFont.truetype(font_path, 52)
	body_font = ImageFont.truetype(font_path, 42)
	draw.text((80, 90), title, fill="black", font=title_font)
	x, y = 100, 360
	draw.text((x, y), line, fill="black", font=body_font)
	prefix = line.index(target)
	prefix_width = draw.textlength(line[:prefix], font=body_font)
	left = x + prefix_width
	top_box = draw.textbbox((left, y), target, font=body_font)
	expected = {
		"x": top_box[0] / image.width,
		"y": top_box[1] / image.height,
		"w": (top_box[2] - top_box[0]) / image.width,
		"h": (top_box[3] - top_box[1]) / image.height,
	}
	return image, expected

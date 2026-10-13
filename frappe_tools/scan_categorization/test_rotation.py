import base64
from unittest.mock import patch

import cv2
import numpy as np
from frappe.tests import UnitTestCase

from frappe_tools.scan_categorization.rotation import correct_image, detect_skew


def page_image() -> np.ndarray:
	image = np.full((600, 900, 3), 255, dtype=np.uint8)
	for y in range(100, 500, 80):
		cv2.putText(image, "LOCAL TEST INVOICE 42 - TOTAL 1250", (60, y - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
		cv2.line(image, (60, y), (820, y), (0, 0, 0), 2)
	return image


def data_url(image: np.ndarray, extension=".png") -> str:
	mime = "image/jpeg" if extension == ".jpg" else "image/png"
	return f"data:{mime};base64," + base64.b64encode(cv2.imencode(extension, image)[1]).decode()


class TestRotation(UnitTestCase):
	def test_preserves_image_format(self):
		for extension, signature in ((".jpg", b"\xff\xd8\xff"), (".png", b"\x89PNG\r\n\x1a\n")):
			with self.subTest(extension=extension):
				url = data_url(page_image(), extension)
				with patch.object(cv2, "imencode", wraps=cv2.imencode) as encode:
					corrected = correct_image(url, 90)
				self.assertTrue(corrected.startswith(signature))
				self.assertEqual(encode.call_args.args[0], extension)
				self.assertEqual(encode.call_args.args[2], [cv2.IMWRITE_JPEG_QUALITY, 92] if extension == ".jpg" else [])
				actual = cv2.imdecode(np.frombuffer(corrected, dtype=np.uint8), cv2.IMREAD_COLOR)
				self.assertEqual(actual.shape, (900, 600, 3))

	def test_quarter_turns(self):
		image = page_image()
		for angle in (90, 180, 270):
			with self.subTest(angle=angle):
				crooked = np.rot90(image, angle // 90)
				corrected = correct_image(data_url(crooked), angle)
				actual = cv2.imdecode(np.frombuffer(corrected, dtype=np.uint8), cv2.IMREAD_COLOR)
				np.testing.assert_array_equal(actual, image)

	def test_deskew_both_directions(self):
		image = page_image()
		for angle in (-8, 6):
			with self.subTest(angle=angle):
				matrix = cv2.getRotationMatrix2D((450, 300), angle, 1)
				tilted = cv2.warpAffine(image, matrix, (900, 600), borderValue=(255, 255, 255))
				self.assertAlmostEqual(detect_skew(tilted), -angle, delta=0.5)
				corrected = correct_image(data_url(tilted), 0)
				actual = cv2.imdecode(np.frombuffer(corrected, dtype=np.uint8), cv2.IMREAD_COLOR)
				self.assertLess(abs(detect_skew(actual)), 0.5)
				self.assertGreaterEqual(actual.shape[0], image.shape[0])

	def test_no_rotation_for_upright_or_out_of_range_skew(self):
		url = data_url(page_image())
		self.assertIsNone(correct_image(url, 0))
		for angle in (0.49, -0.49, 15.01, -16, 30):
			with patch("frappe_tools.scan_categorization.rotation.detect_skew", return_value=angle):
				self.assertIsNone(correct_image(url, 0))

	def test_invalid_image_or_rotation(self):
		with self.assertRaises(ValueError):
			correct_image("data:image/png;base64,YmFk", 0)
		with self.assertRaises(ValueError):
			correct_image(data_url(page_image()), 45)

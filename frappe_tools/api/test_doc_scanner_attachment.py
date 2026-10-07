import re
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from frappe_tools.api import doc_scanner

PNG_DATA_URL = (
	"data:image/png;base64,"
	"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
UUID_PNG = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.png"


class TestDocScannerAttachmentName(FrappeTestCase):
	def test_stem_uses_doctype_docname_and_unique_suffix(self):
		stem = doc_scanner.get_direct_attachment_file_stem("Sales Invoice", "SINV/2026/0001")
		self.assertRegex(stem, r"^Sales_Invoice_SINV_2026_0001_[a-f0-9]{10}$")

	def test_stems_are_unique_for_the_same_document(self):
		first = doc_scanner.get_direct_attachment_file_stem("Item", "ITEM-1")
		second = doc_scanner.get_direct_attachment_file_stem("Item", "ITEM-1")
		self.assertNotEqual(first, second)

	@patch("frappe_tools.api.doc_scanner.save_file_always_new")
	def test_upload_uses_given_stem(self, save_file):
		doc_scanner.create_image_upload(PNG_DATA_URL, "Item", "ITEM-1", fieldname="image", file_stem="Item_ITEM-1_abc")
		self.assertEqual(save_file.call_args.kwargs["fname"], "Item_ITEM-1_abc.png")

	@patch("frappe_tools.api.doc_scanner.save_file_always_new")
	def test_scanned_page_upload_keeps_random_name(self, save_file):
		doc_scanner.create_image_upload(PNG_DATA_URL, "Scanned Document Detail", "SDD-1")
		self.assertTrue(re.fullmatch(UUID_PNG, save_file.call_args.kwargs["fname"]))

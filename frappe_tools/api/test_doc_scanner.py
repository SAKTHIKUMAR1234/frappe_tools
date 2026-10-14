import re

from frappe.tests import UnitTestCase

from frappe_tools.api.doc_scanner import attachment_file_stem


class TestAttachmentFileStem(UnitTestCase):
	def test_names_file_by_document_and_field(self):
		stem = attachment_file_stem("Purchase Invoice", "ACC-PINV-2026-00012", "bill_copy")

		self.assertRegex(stem, r"^Purchase-Invoice_ACC-PINV-2026-00012_bill_copy_[0-9a-f]{8}$")

	def test_replaces_characters_unsafe_in_a_file_name(self):
		stem = attachment_file_stem("Stock Entry", "SE/26/0001 (A)", "image")

		self.assertTrue(stem.startswith("Stock-Entry_SE-26-0001-A_image_"))
		self.assertIsNone(re.search(r"[\s/()]", stem))

	def test_each_name_is_unique(self):
		self.assertNotEqual(attachment_file_stem("Item", "X", "image"), attachment_file_stem("Item", "X", "image"))

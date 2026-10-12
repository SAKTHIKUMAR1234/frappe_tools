# Copyright (c) 2025, sakthi123msd@gmail.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from frappe_tools.frappe_tools.doctype.scanned_document import scanned_document


class TestScannedDocument(FrappeTestCase):
	def test_scan_pdf_opens_inline_in_browser(self):
		with (
			patch.object(scanned_document, "get_print_html", return_value="<p>scan</p>"),
			patch.object(scanned_document, "get_pdf", return_value=b"%PDF-1.4"),
		):
			scanned_document.get_scan_pdf("SCAN-0001")

		self.assertEqual(frappe.local.response.type, "pdf")
		self.assertEqual(frappe.local.response.filename, "Document_Scan_SCAN-0001.pdf")
		self.assertEqual(frappe.local.response.filecontent, b"%PDF-1.4")

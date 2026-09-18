"""Offline regressions for extraction page loading; no storage or model calls."""
import base64
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe
from frappe_tools.extractors.pipeline import file_to_data_url


class TestPageReader(TestCase):
    def test_private_image_uses_file_name_mime(self):
        file_doc = MagicMock(file_name='page.png')
        file_doc.get_content.return_value = b'image bytes'
        with patch.object(frappe, 'db', MagicMock()), patch.object(frappe, 'get_doc', return_value=file_doc), \
             patch('frappe_tools.i2a.extract._is_s3_file', return_value=False):
            url = file_to_data_url('/private/files/page.png')
        self.assertEqual(url, 'data:image/png;base64,' + base64.b64encode(b'image bytes').decode())

    def test_proxy_url_uses_shared_storage_reader(self):
        with patch('frappe_tools.i2a.extract._read_file', return_value=(b'png', 'image/png')) as reader:
            url = file_to_data_url('/api/method/frappe_s3_integration.download?file_id=FILE')
        reader.assert_called_once()
        self.assertTrue(url.startswith('data:image/png;base64,'))

    def test_pdf_is_not_mislabeled_as_image(self):
        with patch('frappe_tools.i2a.extract._read_file', return_value=(b'%PDF', 'application/pdf')):
            with self.assertRaisesRegex(ValueError, 'scanned page images'):
                file_to_data_url('/private/files/invoice.pdf')

    def test_image_data_url_passes_through(self):
        self.assertEqual(file_to_data_url('data:image/jpeg;base64,YQ=='), 'data:image/jpeg;base64,YQ==')

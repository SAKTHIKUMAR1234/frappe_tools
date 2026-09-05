from pathlib import Path
from unittest.mock import patch

import fitz
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.file_manager import save_file

from frappe_tools.extractors import service


class TestExtractionService(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.fixture = Path(__file__).parent / "test_fixtures" / "handwritten_packing_slip.png"

	@patch("frappe_tools.extractors.service.pipeline.enqueue_extraction")
	def test_document_paths_create_local_private_page_and_enqueue(self, enqueue):
		enqueue.return_value = {"job_id": "job-1", "queue": "long", "page_count": 1, "timeout_seconds": 60}
		result = service.enqueue_document_paths("Purchase Invoice", [self.fixture], enqueue_after_commit=False)
		doc = frappe.get_doc("Document Extraction", result["extraction"])
		file_doc = frappe.get_doc("File", {"file_url": doc.pages[0].image})

		self.assertEqual(doc.status, "Queued")
		self.assertEqual(len(doc.pages), 1)
		self.assertTrue(file_doc.is_private)
		self.assertTrue(file_doc.file_url.startswith("/private/files/"))
		self.assertTrue(Path(file_doc.get_full_path()).is_file())
		enqueue.assert_called_once_with(doc.name, 1, enqueue_after_commit=False)

	@patch("frappe_tools.extractors.classification.enqueue")
	def test_targetless_intake_routes_to_classification(self, classify):
		classify.return_value = {
			"extraction": "DOCEXT-AUTO", "status": "Classifying", "job_id": "classification-job",
		}
		doc = service.create_extraction(None)
		service.stage_local_paths(doc, [self.fixture])

		result = service.enqueue(doc, enqueue_after_commit=False)

		self.assertEqual(result["status"], "Classifying")
		classify.assert_called_once()

	def test_local_path_service_rejects_remote_urls(self):
		doc = service.create_extraction("Purchase Invoice")
		with self.assertRaises(frappe.ValidationError):
			service.stage_local_paths(doc, ["https://production.example/invoice.jpg"])

	def test_pdf_is_rendered_locally_into_private_image_pages(self):
		pdf = fitz.open()
		page = pdf.new_page(width=595, height=842)
		page.insert_text((72, 72), "Supplier Invoice INV-007")
		content = pdf.tobytes()
		pdf.close()

		pages = service.document_pages(content, ".pdf", "invoice.pdf")

		self.assertEqual(len(pages), 1)
		self.assertEqual(pages[0]["suffix"], ".png")
		self.assertTrue(pages[0]["content"].startswith(b"\x89PNG"))
		self.assertGreater(pages[0]["width"], 1000)

	def test_pdf_page_limit_fails_before_rendering(self):
		pdf = fitz.open()
		for _ in range(service.MAX_PAGES + 1):
			pdf.new_page(width=100, height=100)
		content = pdf.tobytes()
		pdf.close()

		with self.assertRaises(frappe.ValidationError):
			service.document_pages(content, ".pdf", "too-many-pages.pdf")

	@patch("frappe_tools.extractors.service.frappe.enqueue")
	def test_file_url_capture_tracks_original_and_auto_queues(self, enqueue):
		file_doc = save_file("lr-source.png", self.fixture.read_bytes(), None, None, is_private=1)

		doc = service.create_from_file_urls("LR Processing Entry", [file_doc.file_url], auto_process=True)
		doc.reload()

		self.assertEqual(doc.status, "Preparing")
		self.assertEqual(doc.source_count, 1)
		self.assertEqual(doc.source_files[0].source_file, file_doc.file_url)
		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.kwargs["job_id"], f"document-source-preparation::{doc.name}")

	@patch("frappe_tools.extractors.service.pipeline.enqueue_extraction")
	def test_attached_image_and_pdf_preserve_source_to_page_lineage(self, enqueue):
		enqueue.return_value = {"job_id": "extract-job", "queue": "long", "page_count": 3, "timeout_seconds": 180}
		image = save_file("source-image.png", self.fixture.read_bytes(), None, None, is_private=1)
		pdf = fitz.open()
		for text in ("Supplier Invoice INV-007", "Invoice continuation"):
			page = pdf.new_page(width=595, height=842)
			page.insert_text((72, 72), text)
		pdf_file = save_file("source-invoice.pdf", pdf.tobytes(), None, None, is_private=1)
		pdf.close()
		doc = service.create_from_file_urls(
			"Purchase Invoice", [image.file_url, pdf_file.file_url], auto_process=False,
		)
		frappe.db.set_value("Document Extraction", doc.name, "status", "Preparing")

		result = service.prepare_attached_sources(doc.name)
		doc.reload()

		self.assertEqual(result["status"], "Queued")
		self.assertEqual(doc.status, "Queued")
		self.assertEqual(doc.source_count, 2)
		self.assertEqual(doc.page_count, 3)
		self.assertEqual((doc.source_files[0].page_from, doc.source_files[0].page_to), (1, 1))
		self.assertEqual((doc.source_files[1].page_from, doc.source_files[1].page_to), (2, 3))
		self.assertEqual([row.source_page_no for row in doc.pages], [1, 1, 2])
		self.assertTrue(all(row.source_file for row in doc.pages))
		self.assertEqual([row.status for row in doc.source_files], ["Ready", "Ready"])
		self.assertEqual([row.stage for row in doc.processing_events], ["Source Preparation", "Source Preparation", "Extraction"])

	def test_file_url_capture_rejects_s3_proxy(self):
		with self.assertRaises(frappe.ValidationError):
			service.create_from_file_urls(
				"Purchase Invoice",
				["/api/method/frappe_s3_integration.s3_core.serve_file/source.pdf?file_id=x"],
				auto_process=False,
			)

	def test_existing_scanned_document_detail_is_reused_as_source(self):
		file_doc = save_file("existing-scan.png", self.fixture.read_bytes(), None, None, is_private=1)
		scan = frappe.get_doc({
			"doctype": "Scanned Document",
			"_doctype": "User",
			"_docname": "Administrator",
		})
		scan.flags.ignore_mandatory = True
		scan.insert(ignore_permissions=True)
		detail = frappe.get_doc({
			"doctype": "Scanned Document Detail",
			"scanner_document": scan.name,
			"page_no": 1,
			"attachment": file_doc.file_url,
			"layout_type": "Single Page",
			"page_type": "Front",
		})
		detail.flags.ignore_mandatory = True
		detail.insert(ignore_permissions=True)

		doc = service.create_from_scanned_document_details(
			"Purchase Invoice", [detail.name], auto_process=False,
		)

		self.assertEqual(doc.source_files[0].source_file, file_doc.file_url)
		self.assertEqual(doc.source_files[0].scanned_document_detail, detail.name)
		self.assertEqual(frappe.db.get_value("Scanned Document Detail", detail.name, "scanner_document"), scan.name)

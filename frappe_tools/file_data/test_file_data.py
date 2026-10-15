import os
from copy import deepcopy
from io import BytesIO
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from PIL import Image

from frappe_tools.ai import client
from frappe_tools.ai.client import DeadlineReached, bounded_options
from frappe_tools.ai.test_providers import ai_task_rows
from frappe_tools.file_data import reader
from frappe_tools.file_data.documents import files_for_document, get_document_pages
from frappe_tools.file_data.extraction import PAGE_SCHEMA
from frappe_tools.patches import move_scan_page_data_to_file
from frappe_tools.utils import llm, save_file_always_new

DETAIL = "Scanned Document Detail"
CALL_VISION = llm.call_vision


def image_content(fmt="PNG") -> bytes:
	buffer = BytesIO()
	Image.frombytes("RGB", (4, 4), os.urandom(48)).save(buffer, fmt)
	return buffer.getvalue()


def pdf_content(pages: int) -> bytes:
	images = [Image.frombytes("RGB", (40, 40), os.urandom(4800)) for _ in range(pages)]
	buffer = BytesIO()
	images[0].save(buffer, "PDF", save_all=True, append_images=images[1:])
	return buffer.getvalue()


class TestFileData(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.addCleanup(frappe.db.rollback)
		self.enterContext(patch.object(frappe.db, "commit"))
		self.vision = self.enterContext(patch.object(llm, "call_vision", side_effect=self.read_image))
		self.prefix = "local-file-data-" + frappe.generate_hash(length=8)
		self.todo = frappe.get_doc({"doctype": "ToDo", "description": self.prefix}).insert()

	def read_image(self, images, *args, **kwargs):
		self.assertEqual(len(images), 1)
		self.assertTrue(images[0].startswith("data:image/"))
		page = {**deepcopy(PAGE_SCHEMA), "document_number": f"{self.prefix}-{self.vision.call_count}"}
		return {"data": {"pages": [page]}, "model": "local/test-model"}

	def attach(self, content, extension, field=None, doctype="ToDo", name=None):
		return save_file_always_new(f"{self.prefix}.{extension}", content, doctype, name or self.todo.name,
			is_private=1, df=field)

	def stored(self, file):
		return frappe.db.get_value("File", file.name, ["ai_page_data", "ai_page_count", "ai_page_model"], as_dict=True)

	def test_image_file_is_read_once_and_stored_on_the_file(self):
		file = self.attach(image_content(), "png")
		pages = get_document_pages("ToDo", self.todo.name)
		self.assertEqual((len(pages), pages[0]["page_no"]), (1, 1))
		stored = self.stored(file)
		self.assertEqual(frappe.parse_json(stored.ai_page_data)["pages"][0]["document_number"], f"{self.prefix}-1")
		self.assertEqual((stored.ai_page_count, stored.ai_page_model), (1, "local/test-model"))
		self.assertEqual(self.vision.call_args.kwargs["reference_name"], self.todo.name)
		self.assertEqual(get_document_pages("ToDo", self.todo.name), pages)
		self.vision.assert_called_once()

	def test_pdf_pages_are_rendered_and_read_one_by_one(self):
		file = self.attach(pdf_content(2), "pdf")
		self.assertEqual([page["page_no"] for page in reader.read_file(file.name)["pages"]], [1, 2])
		self.assertEqual(self.vision.call_count, 2)
		self.assertTrue(all(call.args[0][0].startswith("data:image/jpeg") for call in self.vision.call_args_list))
		self.assertEqual(self.stored(file).ai_page_count, 2)

	def test_same_content_is_copied_without_a_model_call(self):
		content = image_content()
		first = self.attach(content, "png")
		reader.read_file(first.name)
		other = frappe.get_doc({"doctype": "ToDo", "description": self.prefix}).insert()
		copy = self.attach(content, "png", name=other.name)
		self.assertEqual(reader.read_file(copy.name), frappe.parse_json(self.stored(first).ai_page_data))
		self.assertEqual(self.stored(copy).ai_page_model, "local/test-model")
		self.vision.assert_called_once()

	def test_data_stored_by_another_worker_during_the_read_is_returned(self):
		file = self.attach(image_content(), "png")
		other = {"pages": [{**deepcopy(PAGE_SCHEMA), "document_number": "other-worker", "page_no": 1}]}

		def read_while_another_worker_saves(*args, **kwargs):
			frappe.db.set_value("File", file.name, reader.data_values(other, "local/other"), update_modified=False)
			return self.read_image(*args, **kwargs)

		self.vision.side_effect = read_while_another_worker_saves
		self.assertEqual(reader.read_file(file.name), other)
		self.assertEqual(self.stored(file).ai_page_model, "local/other")

	def test_no_file_read_starts_after_the_deadline(self):
		self.attach(image_content(), "png")
		with self.assertRaisesRegex(TimeoutError, "time limit"):
			get_document_pages("ToDo", self.todo.name, deadline=0.001)
		self.vision.assert_not_called()
		pages = get_document_pages("ToDo", self.todo.name)
		self.assertEqual(get_document_pages("ToDo", self.todo.name, deadline=0.001), pages)

	def test_deadline_stops_a_pdf_between_pages_and_keeps_it_unread(self):
		file = self.attach(pdf_content(3), "pdf")
		# The clock passes the deadline once the first page is read.
		with (
			patch.object(reader.time, "monotonic", side_effect=lambda: 10 if self.vision.call_count else 0),
			self.assertRaisesRegex(DeadlineReached, "time limit"),
		):
			get_document_pages("ToDo", self.todo.name, deadline=5)
		self.vision.assert_called_once()
		self.assertEqual(self.vision.call_args.kwargs["deadline"], 5)
		stored = frappe.db.get_value("File", file.name, ["ai_page_data", "ai_page_error", "ai_page_attempts"], as_dict=True)
		self.assertEqual((stored.ai_page_data, stored.ai_page_error, stored.ai_page_attempts), (None, None, 0))

	def test_budget_ending_during_a_request_keeps_the_file_unread(self):
		file = self.attach(image_content(), "png")
		self.vision.side_effect = CALL_VISION
		settings = frappe._dict(enable=1, max_attempts=1, task_models=ai_task_rows("local/test-model"))
		now = [1000.0]

		def timed_out(*args, **kwargs):
			now[0] += 30
			raise client.requests.Timeout("Local read timeout")

		with (
			patch.object(llm, "ensure_ready", return_value=settings),
			patch.object(client.requests, "post", side_effect=timed_out),
			patch.object(client.time, "monotonic", side_effect=lambda: now[0]),
			self.assertRaises(DeadlineReached),
		):
			get_document_pages("ToDo", self.todo.name, deadline=now[0] + 10)
		stored = frappe.db.get_value("File", file.name, ["ai_page_data", "ai_page_error", "ai_page_attempts"], as_dict=True)
		self.assertEqual((stored.ai_page_data, stored.ai_page_error, stored.ai_page_attempts), (None, None, 0))

	def test_request_timeout_ends_by_the_deadline(self):
		self.assertEqual(bounded_options({"timeout": 120}, None), {"timeout": 120})
		with patch.object(client.time, "monotonic", return_value=100):
			self.assertEqual(bounded_options({"timeout": 120}, 130), {"timeout": 30})
			self.assertAlmostEqual(bounded_options({"timeout": 120}, 100.4)["timeout"], 0.4)
			with self.assertRaises(DeadlineReached):
				bounded_options({"timeout": 120}, 100)

	def test_document_files_are_images_and_pdfs_in_field_then_creation_order(self):
		late = self.attach(image_content(), "png")
		self.attach(b"local text", "txt")
		pdf = self.attach(pdf_content(1), "pdf", field="attachment")
		early = self.attach(image_content("JPEG"), "jpg", field="attachment")
		frappe.db.set_value("File", early.name, "creation", "2000-01-01")
		self.assertEqual(files_for_document("ToDo", self.todo.name), [late.name, early.name, pdf.name])
		pages = get_document_pages("ToDo", self.todo.name)
		self.assertEqual([page["page_no"] for page in pages], [1, 2, 3])
		self.assertEqual(self.vision.call_count, 3)

	def test_document_pages_need_read_permission(self):
		self.attach(image_content(), "png")
		with (
			patch.object(frappe, "has_permission", side_effect=frappe.PermissionError) as permission,
			self.assertRaises(frappe.PermissionError),
		):
			get_document_pages("ToDo", self.todo.name)
		permission.assert_called_once_with("ToDo", "read", doc=self.todo.name, throw=True)
		self.vision.assert_not_called()

	def test_unreadable_file_records_an_error_and_fails_the_document(self):
		file = self.attach(image_content(), "png")
		self.vision.side_effect = RuntimeError("Local provider failure")
		with self.assertRaisesRegex(ValueError, "Local provider failure"):
			get_document_pages("ToDo", self.todo.name)
		error = frappe.db.get_value("File", file.name, ["ai_page_error", "ai_page_attempts"], as_dict=True)
		self.assertEqual(error.ai_page_attempts, 1)
		self.assertIsNone(reader.read_file(file.name))
		self.vision.assert_called_once()

	def test_patch_moves_detail_page_data_to_its_file_once(self):
		if not all(frappe.db.has_column(DETAIL, column) for column in ("ai_page_data", "ai_page_data_attachment")):
			self.skipTest("Old detail columns are not on this site")
		detail = frappe.get_doc({"doctype": DETAIL, "page_no": 1}).insert(ignore_mandatory=True)
		file = self.attach(image_content(), "png", field="attachment", doctype=DETAIL, name=detail.name)
		page = {**deepcopy(PAGE_SCHEMA), "page_no": 4, "document_number": self.prefix}
		frappe.db.sql(
			f"update `tab{DETAIL}` set attachment=%s, ai_page_data=%s, ai_page_data_attachment=%s where name=%s",
			(file.file_url, frappe.as_json(page), file.file_url, detail.name),
		)
		with patch.object(move_scan_page_data_to_file, "create_file_fields"):
			move_scan_page_data_to_file.execute()
			stored = self.stored(file)
			self.assertEqual(frappe.parse_json(stored.ai_page_data), {"pages": [{**page, "page_no": 1}]})
			self.assertEqual(stored.ai_page_count, 1)
			frappe.db.set_value("File", file.name, "ai_page_model", "local/kept")
			move_scan_page_data_to_file.execute()
		self.assertEqual(self.stored(file).ai_page_model, "local/kept")
		self.vision.assert_not_called()


class TestPageTextLines(FrappeTestCase):
	def test_text_given_as_lines_is_joined(self):
		from frappe_tools.file_data.extraction import _validate_pages

		page = {**deepcopy(PAGE_SCHEMA), "items": [], "handwritten": [], "item_table": None,
			"full_text": ["TAX INVOICE", "Total 100"]}
		self.assertEqual(_validate_pages([page], 1)[0]["full_text"], "TAX INVOICE\nTotal 100")

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from frappe_tools.frappe_tools.doctype.scanned_document_detail.scanned_document_detail import (
	DETAIL,
	SCAN,
	attach_files,
)
from frappe_tools.utils import save_file_always_new


class TestOriginalRetention(UnitTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.addCleanup(frappe.db.rollback)
		self.enterContext(patch.object(frappe.db, "commit"))
		self.prefix = "local-retention-test-" + frappe.generate_hash(length=8)
		self.todo = frappe.get_doc({"doctype": "ToDo", "description": self.prefix}).insert()
		self.scan = self.make_scan()
		self.content = self.prefix.encode()

	def make_scan(self):
		layout = frappe.get_doc({"doctype": "Document Scanner Layout", "layout_name": frappe.generate_hash(length=12),
			"layout_doctype": "ToDo"}).insert()
		return frappe.get_doc({"doctype": SCAN, "_doctype": "ToDo", "_docname": self.todo.name,
			"scanner_layout": layout.name}).insert()

	def make_detail(self, scan, url):
		# Scanner code sets attachments with db_set, so core's on_update File copy never runs.
		detail = frappe.get_doc({"doctype": DETAIL, "scanner_document": scan.name, "page_no": 1}).insert(
			ignore_mandatory=True)
		detail.db_set("attachment", url)
		return detail

	def make_file(self, doctype, name, field=None):
		return save_file_always_new(f"{self.prefix}.txt", self.content, doctype, name, is_private=1, df=field)

	def owner_of(self, file):
		return frappe.db.get_value("File", file.name, ["attached_to_doctype", "attached_to_name", "attached_to_field"])

	def test_shared_url_file_of_unrelated_doctype_is_never_repointed(self):
		foreign = self.make_file("ToDo", self.todo.name, "description")
		detail = self.make_detail(self.scan, foreign.file_url)
		attach_files(detail)
		self.assertEqual(self.owner_of(foreign), ("ToDo", self.todo.name, "description"))

	def test_scan_file_is_attached_to_detail_but_foreign_file_is_kept(self):
		foreign = self.make_file("ToDo", self.todo.name)
		scan_file = self.make_file(SCAN, self.scan.name)
		self.assertEqual(foreign.file_url, scan_file.file_url)
		detail = self.make_detail(self.scan, scan_file.file_url)
		attach_files(detail)
		self.assertEqual(self.owner_of(scan_file), (DETAIL, detail.name, "attachment"))
		self.assertEqual(self.owner_of(foreign), ("ToDo", self.todo.name, None))

	def test_file_of_detail_from_another_scan_is_not_taken(self):
		other_detail = self.make_detail(self.make_scan(), None)
		file = self.make_file(DETAIL, other_detail.name, "attachment")
		attach_files(self.make_detail(self.scan, file.file_url))
		self.assertEqual(self.owner_of(file), (DETAIL, other_detail.name, "attachment"))

	def test_trash_keeps_file_for_live_row_of_another_scan(self):
		old = self.make_detail(self.scan, None)
		file = self.make_file(DETAIL, old.name, "attachment")
		live = self.make_detail(self.make_scan(), file.file_url)
		old.db_set("is_deleted", 1)
		frappe.delete_doc(DETAIL, old.name)
		self.assertEqual(self.owner_of(file), (DETAIL, live.name, "attachment"))

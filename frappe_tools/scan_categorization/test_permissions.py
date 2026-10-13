"""Local database regressions for evidence access and durable verifier output."""

import json
from io import BytesIO
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase
from openpyxl import load_workbook

from frappe_tools.frappe_tools.doctype.scanned_document_ai_verifier import (
	scanned_document_ai_verifier as verifier,
)
from frappe_tools.utils import llm


class TestEvidencePermissions(UnitTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.addCleanup(frappe.db.rollback)
		self.addCleanup(frappe.set_user, "Administrator")
		self.enterContext(patch.object(frappe.db, "commit"))
		self.prefix = "local-scan-test-" + frappe.generate_hash(length=8)
		self.users = []
		for suffix in ("owner", "other"):
			user = frappe.get_doc({
				"doctype": "User", "email": f"{self.prefix}-{suffix}@example.invalid",
				"first_name": "Local Scan Test", "send_welcome_email": 0,
				"roles": [{"role": "Accounts Manager"}, {"role": "Scanner User"}],
			}).insert()
			self.users.append(user.name)
		self.doc = frappe.get_doc({
			"doctype": verifier.DOCTYPE, "reference_doctype": "Purchase Invoice",
			"prompt": "Compare quantity", "max_documents": 2, "filters_data": "[]",
			"owner": self.users[0], "status": "Failed", "last_run_id": "local-current-run",
			"columns": json.dumps([*verifier.FIXED_COLUMNS, {"key": "quantity", "label": "Quantity"}]),
		}).insert()

		self.doc.db_set("owner", self.users[0])

	def checkpoint(self, run_id, quantity):
		doc = frappe.copy_doc(self.doc)
		doc.name = self.doc.name
		doc.last_run_id = run_id
		verifier._checkpoint_row(doc, {"document": "LOCAL-PI-42", "status": "Completed", "quantity": quantity})
		return frappe.get_last_doc("Document AI Call Log", filters={"reference_name": doc.name, "run_id": run_id})

	def test_owner_only_verifier_logs_and_private_workbook(self):
		log = self.checkpoint(self.doc.last_run_id, 12.5)
		url = verifier.build_excel(self.doc.name)
		file = frappe.get_doc("File", {"file_url": url})
		self.assertTrue(file.is_private)
		self.assertEqual((file.attached_to_doctype, file.attached_to_name, file.attached_to_field),
			(verifier.DOCTYPE, self.doc.name, "result_file"))
		for user, allowed in zip(self.users, (True, False), strict=True):
			frappe.set_user(user)
			self.assertEqual(bool(frappe.has_permission(verifier.DOCTYPE, "read", doc=self.doc.name)), allowed)
			self.assertEqual(bool(frappe.has_permission("Document AI Call Log", "read", doc=log.name)), allowed)
			self.assertEqual(bool(file.is_downloadable()), allowed)
			self.assertEqual(bool(frappe.get_list("Document AI Call Log", filters={"name": log.name})), allowed)
		frappe.set_user(self.users[0])
		self.assertFalse(frappe.has_permission("Document AI Call Log", "write", doc=log.name))
		self.assertEqual(frappe.get_list("Document AI Call Log", filters={"reference_name": self.doc.name}, pluck="name"),
			[log.name])
		frappe.set_user(self.users[1])
		self.assertFalse(frappe.get_list("Document AI Call Log", filters={"reference_name": self.doc.name}))
		with self.assertRaises(frappe.PermissionError):
			verifier.build_excel(self.doc.name)
		with self.assertRaises(frappe.PermissionError):
			verifier.run_verifier(self.doc.name)

	def test_scan_logs_require_linked_document_access(self):
		secret = frappe.get_doc({"doctype": "ToDo", "description": "Local private scan evidence: quantity 12.5",
			"allocated_to": self.users[0]}).insert()
		layout = frappe.get_doc({"doctype": "Document Scanner Layout", "layout_name": self.prefix,
			"layout_doctype": "ToDo"}).insert()
		scan = frappe.get_doc({"doctype": "Scanned Document", "_doctype": "ToDo", "_docname": secret.name,
			"scanner_layout": layout.name}).insert()
		llm.log_call(None, "ToDo", None, "Success", None, 0, {}, {}, {"private": "evidence"}, None,
			reference_doctype=scan.doctype, reference_name=scan.name)
		log = frappe.get_last_doc("Document AI Call Log", filters={"reference_name": scan.name})
		frappe.set_user(self.users[1])
		self.assertFalse(frappe.has_permission("ToDo", "read", doc=secret.name))
		self.assertFalse(frappe.has_permission("Document AI Call Log", "read", doc=log.name))
		self.assertFalse(frappe.get_list("Document AI Call Log", filters={"name": log.name}))
		frappe.set_user(self.users[0])
		self.assertTrue(frappe.has_permission("ToDo", "read", doc=secret.name))
		self.assertTrue(frappe.has_permission("Document AI Call Log", "read", doc=log.name))
		self.assertFalse(frappe.has_permission("Document AI Call Log", "write", doc=log.name))
		frappe.set_user("Administrator")
		self.assertTrue(frappe.has_permission("Document AI Call Log", "read", doc=log.name))
		scan.delete()
		self.assertEqual(log.reload().reference_name, scan.name)

	def test_partial_excel_uses_only_checkpoint_rows_of_current_run(self):
		self.checkpoint("local-old-run", 999)
		self.checkpoint(self.doc.last_run_id, 12.5)
		llm.log_call(None, "Purchase Invoice", None, "Success", None, 0, {}, {}, {"values": {"quantity": 777}}, None,
			phase="Verifier Row", reference_doctype=verifier.DOCTYPE, reference_name=self.doc.name, run_id=self.doc.last_run_id)
		url = verifier.build_excel(self.doc.name)
		file = frappe.get_doc("File", {"file_url": url})
		workbook = load_workbook(BytesIO(file.get_content()))
		self.addCleanup(workbook.close)
		rows = list(workbook.active.values)
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[1][-1], 12.5)
		self.assertEqual(self.doc.reload().status, "Failed")
		self.doc.delete()
		self.assertTrue(frappe.db.exists("Document AI Call Log", {"reference_name": self.doc.name}))

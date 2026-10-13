# Copyright (c) 2026, sakthi123msd@gmail.com and contributors
# For license information, please see license.txt

import json
from io import BytesIO
from unittest.mock import Mock, call, patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils.xlsxutils import make_xlsx
from openpyxl import load_workbook

from frappe_tools.frappe_tools.doctype.scanned_document_ai_verifier import (
	scanned_document_ai_verifier as verifier,
)


class TestScannedDocumentAIVerifier(UnitTestCase):
	def setUp(self):
		self.doc = frappe._dict(
			doctype=verifier.DOCTYPE,
			name="local-test-verifier",
			reference_doctype="Purchase Invoice",
			prompt="Compare scanned weight in kg to total quantity and calculate the difference.",
			filters_data='[["Purchase Invoice", "docstatus", "=", 1]]',
			max_documents=200,
			status="Draft",
			total_documents=0,
			processed_documents=0,
			failed_documents=0,
			check_permission=Mock(),
		)
		self.meta = frappe._dict(
			issingle=0,
			istable=0,
			is_virtual=0,
			fields=[
				frappe._dict(
					fieldname="total_qty", label="Total Quantity", fieldtype="Float"
				),
				frappe._dict(
					fieldname="items",
					label="Items",
					fieldtype="Table",
					options="Purchase Invoice Item",
				),
				frappe._dict(fieldname="terms", label="Terms", fieldtype="Text Editor"),
				frappe._dict(fieldname="secret", label="Secret", fieldtype="Password"),
			],
		)
		self.child_meta = frappe._dict(
			fields=[
				frappe._dict(fieldname="qty", label="Quantity", fieldtype="Float"),
			]
		)
		self.db = self.enterContext(patch.object(verifier.frappe, "db"))
		self.db.exists.return_value = False
		self.enterContext(
			patch.object(
				verifier.frappe, "session", frappe._dict(user="local-test@example.com")
			)
		)
		self.set_user = self.enterContext(patch.object(verifier.frappe, "set_user"))
		self.get_doc = self.enterContext(
			patch.object(verifier.frappe, "get_doc", side_effect=self._get_doc)
		)
		self.enterContext(
			patch.object(verifier.frappe, "get_meta", side_effect=self._get_meta)
		)
		self.has_permission = self.enterContext(
			patch.object(verifier.frappe, "has_permission", return_value=True)
		)
		self.enqueue = self.enterContext(patch.object(verifier.frappe, "enqueue"))
		self.get_list = self.enterContext(
			patch.object(verifier.frappe, "get_list", return_value=[])
		)
		self.get_all = self.enterContext(
			patch.object(verifier.frappe, "get_all", return_value=[])
		)
		self.publish = self.enterContext(
			patch.object(verifier.frappe, "publish_realtime")
		)
		self.enterContext(
			patch.object(
				verifier.frappe,
				"throw",
				side_effect=frappe.ValidationError("invalid run"),
			)
		)
		self.enterContext(
			patch.object(
				verifier.frappe, "get_traceback", return_value="test traceback"
			)
		)
		self.log_error = self.enterContext(patch.object(verifier.frappe, "log_error"))
		self.vision = self.enterContext(patch.object(verifier, "call_vision"))
		self.loader = self.enterContext(
			patch.object(verifier, "load_scanned_document_details")
		)
		self.compress = self.enterContext(
			patch.object(
				verifier, "compress_image", return_value="data:image/jpeg;base64,test"
			)
		)
		self.xlsx = self.enterContext(
			patch.object(
				verifier, "make_xlsx", return_value=BytesIO(b"local-test-xlsx")
			)
		)
		self.save_file = self.enterContext(
			patch.object(
				verifier,
				"save_file",
				return_value=frappe._dict(file_url="/private/files/test.xlsx"),
			)
		)
		self.value_columns = [
			{"key": "scan_weight", "label": "Scanned Weight (kg)"},
			{"key": "total_qty", "label": "Total Quantity"},
			{"key": "difference", "label": "Difference"},
		]
		self.columns = verifier.FIXED_COLUMNS + self.value_columns

	def _get_meta(self, doctype):
		return self.child_meta if doctype == "Purchase Invoice Item" else self.meta

	def _get_doc(self, doctype, name, **kwargs):
		if doctype == verifier.DOCTYPE:
			return self.doc
		return frappe._dict(
			doctype=doctype,
			name=name,
			check_permission=Mock(),
			apply_fieldlevel_read_permissions=Mock(),
			as_dict=Mock(
				return_value={
					"doctype": doctype,
					"name": name,
					"total_qty": 12,
					"items": [{"qty": 12, "owner": "Administrator"}],
					"terms": "<p>terms</p>",
					"secret": "do not send",
					"owner": "Administrator",
					"_comments": "metadata",
					"large_text": "x" * 3000,
				}
			),
		)

	def test_run_requires_write_permission(self):
		self.doc.check_permission.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			verifier.run_verifier(self.doc.name)
		self.doc.check_permission.assert_called_once_with("write")
		self.enqueue.assert_not_called()
		self.db.set_value.assert_not_called()

	def test_run_requires_reference_read_permission(self):
		self.has_permission.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			verifier.run_verifier(self.doc.name)
		self.has_permission.assert_called_once_with(
			"Purchase Invoice", "read", throw=True
		)
		self.enqueue.assert_not_called()

	def test_refuse_double_run(self):
		for status in verifier.ACTIVE_STATUSES:
			with self.subTest(status=status):
				self.doc.status = status
				with self.assertRaises(frappe.ValidationError):
					verifier.run_verifier(self.doc.name)
		self.enqueue.assert_not_called()

	def test_enqueue_as_requesting_user_with_bounded_timeout(self):
		for maximum, timeout in [(1, 600), (10, 1500), (200, 21600)]:
			with self.subTest(maximum=maximum):
				self.doc.update(
					status="Completed", max_documents=maximum, result_file="old.xlsx"
				)
				self.assertEqual(
					verifier.run_verifier(self.doc.name), {"status": "Queued"}
				)
				kwargs = self.enqueue.call_args.kwargs
				self.assertEqual(kwargs["user"], "local-test@example.com")
				self.assertEqual(kwargs["job_id"], f"scan-ai-verifier::{self.doc.name}")
				self.assertEqual(kwargs["queue"], "long")
				self.assertEqual(kwargs["timeout"], timeout)
				self.assertTrue(kwargs["deduplicate"])
				self.assertTrue(kwargs["enqueue_after_commit"])
				self.assertIsNone(self.doc.result_file)
		self.get_doc.assert_called_with(
			verifier.DOCTYPE, self.doc.name, for_update=True
		)

	def test_column_decision_uses_labels_and_fixed_columns_first(self):
		self.vision.return_value = {"data": {"columns": self.value_columns}}
		columns = verifier.decide_columns("Purchase Invoice", self.doc.prompt)
		self.assertEqual(columns, self.columns)
		images, _, prompt = self.vision.call_args.args
		self.assertEqual(images, [])
		payload = json.loads(prompt)
		self.assertEqual(payload["prompt"], self.doc.prompt)
		self.assertEqual(payload["fields"][0]["label"], "Total Quantity")
		self.assertEqual(payload["fields"][1]["fields"][0]["label"], "Quantity")
		self.assertNotIn("Secret", prompt)

	def test_invalid_filters_and_limits_are_rejected_before_enqueue(self):
		for filters in ('{"docstatus": 1}', "null", "invalid JSON"):
			with self.subTest(filters=filters):
				self.doc.filters_data = filters
				with self.assertRaises(frappe.ValidationError):
					verifier.run_verifier(self.doc.name)
		self.doc.filters_data = "[]"
		self.doc.max_documents = 0
		with self.assertRaises(frappe.ValidationError):
			verifier.run_verifier(self.doc.name)
		self.enqueue.assert_not_called()

	def test_scan_lookup_includes_and_deduplicates_lineage(self):
		self.db.exists.return_value = True
		self.get_all.side_effect = [["SCAN-1"], ["SCAN-1", "SCAN-REFERENCED"]]
		self.assertEqual(
			verifier._scan_names("Purchase Invoice", "PI-1"),
			["SCAN-1", "SCAN-REFERENCED"],
		)
		self.get_all.assert_has_calls(
			[
				call(
					"Scanned Document",
					filters={"_doctype": "Purchase Invoice", "_docname": "PI-1"},
					pluck="name",
					order_by="creation asc",
				),
				call(
					"Scanned Document Reference",
					filters={
						"target_doctype": "Purchase Invoice",
						"target_name": "PI-1",
					},
					pluck="scanned_document",
					order_by="creation asc",
				),
			]
		)

	def test_scan_lookup_without_lineage_doctype(self):
		self.get_all.return_value = ["SCAN-1"]
		self.assertEqual(verifier._scan_names("Purchase Invoice", "PI-1"), ["SCAN-1"])
		self.get_all.assert_called_once()

	def test_invalid_columns_fail_instead_of_overwriting_fixed_columns(self):
		self.vision.return_value = {
			"data": {"columns": [{"key": "status", "label": "Override"}]}
		}
		with self.assertRaises(ValueError):
			verifier.decide_columns("Purchase Invoice", self.doc.prompt)

	def test_mixed_batch_filters_rows_excel_and_progress(self):
		self.doc.status = "Queued"
		self.get_list.return_value = ["PI-OK", "PI-NONE", "PI-FAIL", "PI-AFTER"]
		self.get_all.side_effect = [["SCAN-1", "SCAN-2"], [], ["SCAN-BAD"], ["SCAN-3"]]
		self.loader.side_effect = [
			{"attachments": [{"attachment": "/private/files/page1.jpg"}]},
			{"attachments": [{"attachment": "https://local.invalid/signed-page2.jpg"}]},
			RuntimeError("Page unavailable"),
			{"attachments": [{"attachment": "/private/files/page3.jpg"}]},
		]
		self.vision.side_effect = [
			{
				"data": {
					"values": {"scan_weight": 10, "total_qty": 12, "difference": 2},
					"remarks": "2 kg difference",
				}
			},
			{
				"data": {
					"values": {"scan_weight": None, "total_qty": 12},
					"remarks": "Weight unreadable",
				}
			},
		]
		with patch.object(
			verifier, "decide_columns", return_value=self.columns
		) as decide:
			verifier.process_verifier(self.doc.name, "local-test@example.com")
		decide.assert_called_once_with("Purchase Invoice", self.doc.prompt)
		self.get_list.assert_called_once_with(
			"Purchase Invoice",
			filters=json.loads(self.doc.filters_data),
			pluck="name",
			limit=200,
			order_by="creation asc",
		)
		self.assertEqual(json.loads(self.doc.columns), self.columns)
		self.assertEqual(self.doc.status, "Completed")
		self.assertEqual(self.doc.processed_documents, 4)
		self.assertEqual(self.doc.failed_documents, 1)
		self.assertEqual(self.doc.result_file, "/private/files/test.xlsx")
		expected_rows = [
			[column["label"] for column in self.columns],
			["PI-OK", "SCAN-1, SCAN-2", "Completed", "2 kg difference", 10, 12, 2],
			["PI-NONE", "", "No scan", "No scanned pages found.", None, None, None],
			["PI-FAIL", "SCAN-BAD", "Failed", "Page unavailable", None, None, None],
			["PI-AFTER", "SCAN-3", "Completed", "Weight unreadable", None, 12, None],
		]
		self.xlsx.assert_called_once_with(expected_rows, "Verification")
		self.save_file.assert_called_once_with(
			f"{self.doc.name}.xlsx",
			b"local-test-xlsx",
			verifier.DOCTYPE,
			self.doc.name,
			is_private=1,
			df="result_file",
		)
		self.assertEqual(self.vision.call_count, 2)
		images, _, prompt = self.vision.call_args_list[0].args
		self.assertEqual(len(images), 2)
		payload = json.loads(prompt)
		self.assertEqual(payload["columns"], self.value_columns)
		self.assertEqual(
			payload["document"],
			{
				"doctype": "Purchase Invoice",
				"name": "PI-OK",
				"total_qty": 12,
				"items": [{"qty": 12}],
			},
		)
		self.compress.assert_any_call("https://local.invalid/signed-page2.jpg")
		progress = [entry.args[1] for entry in self.publish.call_args_list]
		self.assertEqual(
			[event["processed_documents"] for event in progress], [0, 0, 1, 2, 3, 4, 4]
		)
		self.assertEqual(progress[-1]["status"], "Completed")
		for entry in self.publish.call_args_list:
			self.assertEqual(entry.args[0], verifier.EVENT)
			self.assertEqual(
				entry.kwargs,
				{
					"doctype": verifier.DOCTYPE,
					"docname": self.doc.name,
					"after_commit": True,
				},
			)
		self.assertEqual(self.db.commit.call_count, 7)
		self.set_user.assert_has_calls(
			[call("local-test@example.com"), call("local-test@example.com")]
		)

	def test_vision_error_is_a_failed_row(self):
		self.get_all.return_value = ["SCAN-1"]
		self.loader.return_value = {
			"attachments": [{"attachment": "/private/files/page.jpg"}]
		}
		self.vision.side_effect = RuntimeError("Provider unavailable")
		row = verifier._document_row(self.doc, "PI-FAIL", self.columns)
		self.assertEqual(row["status"], "Failed")
		self.assertEqual(row["remarks"], "Provider unavailable")

	def test_document_permission_failure_does_not_send_evidence(self):
		document = self._get_doc("Purchase Invoice", "PI-DENIED")
		document.check_permission.side_effect = frappe.PermissionError("Read denied")
		self.get_doc.side_effect = None
		self.get_doc.return_value = document
		row = verifier._document_row(self.doc, "PI-DENIED", self.columns)
		self.assertEqual(row["status"], "Failed")
		self.assertEqual(row["remarks"], "Read denied")
		self.loader.assert_not_called()
		self.vision.assert_not_called()

	def test_invalid_vision_values_are_isolated(self):
		self.get_all.return_value = ["SCAN-1"]
		self.loader.return_value = {
			"attachments": [{"attachment": "/private/files/page.jpg"}]
		}
		self.vision.return_value = {"data": {"values": {"scan_weight": {"weight": 10}}}}
		row = verifier._document_row(self.doc, "PI-BAD", self.columns)
		self.assertEqual(row["status"], "Failed")
		self.assertIn("JSON scalars", row["remarks"])

	def test_excel_content_preserves_numbers_nulls_and_literal_text(self):
		rows = [
			{
				"document": "PI-1",
				"scanned_documents": "SCAN-1",
				"status": "Completed",
				"remarks": '=HYPERLINK("https://local.invalid")',
				"scan_weight": 10.5,
				"total_qty": 12,
				"difference": None,
			}
		]
		with (
			patch.object(verifier, "make_xlsx", wraps=make_xlsx),
			patch(
				"frappe.utils.xlsxutils.XLSXStyleBuilder.get_datetime_format",
				return_value="yyyy-mm-dd",
			),
		):
			verifier._save_results(self.doc, self.columns, rows)
		workbook = load_workbook(BytesIO(self.save_file.call_args.args[1]))
		self.addCleanup(workbook.close)
		sheet = workbook["Verification"]
		self.assertEqual(
			list(sheet.values),
			[
				tuple(column["label"] for column in self.columns),
				("PI-1", "SCAN-1", "Completed", rows[0]["remarks"], 10.5, 12, None),
			],
		)
		self.assertEqual(sheet["D2"].data_type, "s")

	def test_empty_batch_creates_header_only_excel(self):
		with patch.object(verifier, "decide_columns", return_value=self.columns):
			verifier.process_verifier(self.doc.name, "local-test@example.com")
		self.xlsx.assert_called_once_with(
			[[column["label"] for column in self.columns]], "Verification"
		)
		self.assertEqual(self.doc.status, "Completed")
		self.assertEqual(self.doc.total_documents, 0)
		self.vision.assert_not_called()

	def test_batch_crash_is_persisted_and_logged(self):
		with (
			patch.object(
				verifier,
				"decide_columns",
				side_effect=RuntimeError("Bad column response"),
			),
			self.assertRaisesRegex(RuntimeError, "Bad column response"),
		):
			verifier.process_verifier(self.doc.name, "local-test@example.com")
		self.assertEqual(self.doc.status, "Failed")
		self.assertEqual(self.doc.error_log, "test traceback")
		self.assertIsNotNone(self.doc.finished_on)
		self.db.rollback.assert_called_once()
		self.log_error.assert_called_once()
		self.assertEqual(self.publish.call_args.args[1]["status"], "Failed")
		self.save_file.assert_not_called()

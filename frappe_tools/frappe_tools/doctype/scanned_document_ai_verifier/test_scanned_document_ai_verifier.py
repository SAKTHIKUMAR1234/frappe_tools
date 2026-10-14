# Copyright (c) 2026, sakthi123msd@gmail.com and contributors
# For license information, please see license.txt

import json
from datetime import datetime
from io import BytesIO
from unittest.mock import Mock, call, patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils.xlsxutils import make_xlsx
from openpyxl import load_workbook

from frappe_tools.frappe_tools.doctype.scanned_document_ai_verifier import (
	scanned_document_ai_verifier as verifier,
)

READ_CHECKPOINTS = verifier._result_rows


class TestScannedDocumentAIVerifier(UnitTestCase):
	def setUp(self):
		self.enterContext(patch.object(verifier, "now_datetime", return_value=datetime(2026, 10, 13, 12)))
		self.doc = frappe._dict(
			doctype=verifier.DOCTYPE,
			name="local-test-verifier",
			reference_doctype="Purchase Invoice",
			prompt="Compare scanned weight in kg to total quantity and calculate the difference.",
			filters_data='[["Purchase Invoice", "docstatus", "=", 1]]',
			max_documents=200,
			status="Queued",
			last_run_id="local-run-token1",
			modified=datetime(2026, 10, 13, 12),
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
		self.checkpoints = []
		self.enterContext(patch.object(verifier, "log_call", side_effect=lambda *a, **kw: self.checkpoints.append(a[8])))
		self.enterContext(patch.object(verifier, "_result_rows", side_effect=lambda doc: self.checkpoints))
		self.enterContext(patch.object(frappe.db, "get_value", side_effect=lambda *a, **kw: self.doc.copy()))
		self.db = Mock()
		for method in ("set_value", "commit", "rollback"):
			self.enterContext(patch.object(frappe.db, method, getattr(self.db, method)))
		self.lineage = self.enterContext(
			patch.object(verifier, "has_scan_reference_doctype", return_value=False)
		)
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
		self.vision = Mock()
		self.enterContext(patch.object(verifier, "call_model", self.vision))
		self.enterContext(patch.object(verifier, "call_agent", self.vision))
		self.enterContext(patch.object(verifier, "get_prompt", return_value="Local verifier prompt."))
		self.page_counts = {}
		self.loader = self.enterContext(patch.object(verifier, "get_document_pages", side_effect=self.stored_pages))
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

	def stored_pages(self, doctype, name):
		return [{"page_no": number, "section": "Invoice", "document_number": f"{name}-{number}"}
			for number in range(1, self.page_counts.get(name, 1) + 1)]

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
				self.assertEqual(kwargs["job_id"], f"scan-ai-verifier::{self.doc.name}::{self.doc.last_run_id}")
				self.assertEqual(kwargs["queue"], "long")
				self.assertEqual(kwargs["timeout"], timeout)
				self.assertEqual(kwargs["run_id"], self.doc.last_run_id)
				self.assertTrue(kwargs["enqueue_after_commit"])
				self.assertIsNone(self.doc.result_file)
		self.get_doc.assert_called_with(
			verifier.DOCTYPE, self.doc.name, for_update=True
		)

	def test_column_decision_uses_labels_and_fixed_columns_first(self):
		self.vision.return_value = {"data": {"columns": self.value_columns}}
		columns = verifier.decide_columns("Purchase Invoice", self.doc.prompt)
		self.assertEqual(columns, self.columns)
		task, _, prompt = self.vision.call_args.args
		self.assertEqual(task, "Verifier")
		self.assertIsNone(self.vision.call_args.kwargs["route"])
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
		self.lineage.return_value = True
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
		self.page_counts["PI-NONE"] = 0
		def pages(doctype, name):
			if name == "SCAN-BAD":
				raise RuntimeError("Page unavailable")
			return self.stored_pages(doctype, name)

		self.loader.side_effect = pages
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
			verifier.process_verifier(self.doc.name, "local-test@example.com", self.doc.last_run_id)
		decide.assert_called_once_with(
			"Purchase Invoice", self.doc.prompt, reference_name=self.doc.name, run_id=self.doc.last_run_id,
			route=None,
		)
		self.assertEqual(self.doc.last_run_id, "local-run-token1")
		for entry in self.vision.call_args_list:
			kwargs = dict(entry.kwargs)
			self.assertIs(kwargs.pop("tools"), verifier.READ_TOOLS)
			self.assertGreater(kwargs.pop("deadline"), 0)
			self.assertEqual(kwargs.pop("schema"), verifier._row_schema(self.value_columns))
			self.assertEqual(kwargs, {
				"target_doctype": "Purchase Invoice", "phase": "Verifier Agent Step", "route": None,
				"reference_doctype": verifier.DOCTYPE, "reference_name": self.doc.name,
				"run_id": self.doc.last_run_id,
			})
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
			["PI-NONE", "", "No attachment", "No scanned pages or attached files found.", None, None, None],
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
		task, system_prompt, prompt = self.vision.call_args_list[0].args
		self.assertEqual(task, "Verifier")
		self.assertTrue(system_prompt.startswith("Local verifier prompt.\n\n"))
		self.assertIn("only from the page data JSON", system_prompt)
		payload = json.loads(prompt)
		self.assertEqual(payload["pages"], [
			{"scanned_document": "SCAN-1", "page_no": 1, "section": "Invoice", "document_number": "SCAN-1-1"},
			{"scanned_document": "SCAN-2", "page_no": 1, "section": "Invoice", "document_number": "SCAN-2-1"},
		])
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
		self.assertEqual(self.db.commit.call_count, 12)
		self.set_user.assert_has_calls(
			[call("local-test@example.com"), call("local-test@example.com")]
		)

	def test_vision_error_is_a_failed_row(self):
		self.get_all.return_value = ["SCAN-1"]
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
			verifier.process_verifier(self.doc.name, "local-test@example.com", self.doc.last_run_id)
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
			verifier.process_verifier(self.doc.name, "local-test@example.com", self.doc.last_run_id)
		self.assertEqual(self.doc.status, "Failed")
		self.assertEqual(self.doc.error_log, "test traceback")
		self.assertIsNotNone(self.doc.finished_on)
		self.db.rollback.assert_called_once()
		self.assertEqual(self.db.commit.call_count, 3)
		self.log_error.assert_called_once()
		self.assertEqual(self.publish.call_args.args[1]["status"], "Failed")
		self.save_file.assert_not_called()

	def test_expired_active_run_can_be_requeued_with_new_token(self):
		for status in verifier.ACTIVE_STATUSES:
			self.doc.update(status=status, modified=datetime(2026, 10, 12), started_on=None)
			old_run = self.doc.last_run_id
			verifier.run_verifier(self.doc.name)
			self.assertNotEqual(self.doc.last_run_id, old_run)
			self.assertEqual(self.doc.status, "Queued")

	def test_stale_job_exits_without_writes(self):
		verifier.process_verifier(self.doc.name, "local-test@example.com", "old-token")
		self.vision.assert_not_called()
		self.db.set_value.assert_not_called()
		self.save_file.assert_not_called()

	def test_replaced_running_job_cannot_finish(self):
		def replace(doc):
			self.doc = frappe._dict(**doc)
			self.doc.last_run_id = "new-token"
		with patch.object(verifier, "_run_batch", side_effect=replace):
			verifier.process_verifier(self.doc.name, "local-test@example.com", self.doc.last_run_id)
		self.assertEqual(self.db.set_value.call_count, 1)  # Initial claim only.
		self.assertEqual(self.doc.status, "Running")
		self.save_file.assert_not_called()

	def test_timeout_and_cancellation_escape_document_isolation(self):
		for exception in (verifier.JobTimeoutException, KeyboardInterrupt, SystemExit):
			with self.subTest(exception=exception):
				self.get_all.return_value = ["SCAN-1"]
				self.loader.side_effect = exception("stop")
				with self.assertRaises(exception):
					verifier._document_row(self.doc, "PI-1", self.columns)
		self.assertFalse(self.checkpoints)

	def test_verifier_sends_every_page_of_every_scan(self):
		self.get_all.return_value = ["SCAN-1", "SCAN-2", "SCAN-3"]
		self.page_counts.update({"SCAN-1": 12, "SCAN-2": 12, "SCAN-3": 3})
		self.vision.return_value = {"data": {"values": {"total_qty": 12}, "remarks": "Matches"}}
		verifier._document_row(self.doc, "PI-1", self.columns)
		self.assertEqual(len(json.loads(self.vision.call_args.args[2])["pages"]), 27)
		self.assertEqual(self.loader.call_args_list, [
			call("Scanned Document", "SCAN-1"), call("Scanned Document", "SCAN-2"), call("Scanned Document", "SCAN-3"),
		])

	def test_document_without_scan_uses_its_attached_files(self):
		self.page_counts["PI-1"] = 22
		self.vision.return_value = {"data": {"values": {"total_qty": 12}, "remarks": "Matches"}}
		row = verifier._document_row(self.doc, "PI-1", self.columns)
		self.loader.assert_called_once_with("Purchase Invoice", "PI-1")
		pages = json.loads(self.vision.call_args.args[2])["pages"]
		self.assertEqual(len(pages), 22)
		self.assertNotIn("scanned_document", pages[0])
		self.assertEqual((row["status"], row["scanned_documents"]), ("Completed", ""))

	def test_lost_permission_at_job_start_fails_the_run(self):
		self.has_permission.side_effect = frappe.PermissionError("Read denied")
		with self.assertRaises(frappe.PermissionError):
			verifier.process_verifier(self.doc.name, "local-test@example.com", self.doc.last_run_id)
		self.assertEqual(self.doc.status, "Failed")
		self.get_list.assert_not_called()
		self.vision.assert_not_called()

	def test_timeout_mid_batch_fails_run_and_keeps_partial_excel(self):
		self.get_list.return_value = ["PI-1", "PI-2", "PI-3"]
		self.get_all.return_value = ["SCAN-1"]
		self.vision.side_effect = [
			{"data": {"values": {"scan_weight": 10, "total_qty": 12, "difference": 2}, "remarks": ""}},
			verifier.JobTimeoutException("job timed out"),
		]
		with (
			patch.object(verifier, "decide_columns", return_value=self.columns),
			self.assertRaises(verifier.JobTimeoutException),
		):
			verifier.process_verifier(self.doc.name, "local-test@example.com", self.doc.last_run_id)
		self.assertEqual(self.doc.status, "Failed")
		self.assertEqual(self.doc.processed_documents, 1)
		self.save_file.assert_not_called()

		self.assertEqual(verifier.build_excel(self.doc.name), "/private/files/test.xlsx")
		self.xlsx.assert_called_once_with(
			[
				[column["label"] for column in self.columns],
				["PI-1", "SCAN-1", "Completed", "", 10, 12, 2],
			],
			"Verification",
		)
		self.db.set_value.assert_called_with(
			verifier.DOCTYPE, self.doc.name, "result_file", "/private/files/test.xlsx"
		)

	def test_build_excel_only_for_failed_runs_with_columns(self):
		for status, columns in [("Completed", "[]"), ("Running", "[]"), ("Failed", None)]:
			with self.subTest(status=status):
				self.doc.update(status=status, columns=columns)
				with self.assertRaises(frappe.ValidationError):
					verifier.build_excel(self.doc.name)
		self.save_file.assert_not_called()

	def test_build_excel_requires_write_permission(self):
		self.doc.update(status="Failed", columns=frappe.as_json(self.columns))
		self.doc.check_permission.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			verifier.build_excel(self.doc.name)
		self.save_file.assert_not_called()

	def test_rows_are_read_from_this_runs_checkpoints(self):
		row = {"document": "PI-1", "status": "Completed", "remarks": "", "total_qty": 12}
		self.get_all.return_value = [frappe._dict(response_payload=frappe.as_json(row))]
		self.assertEqual(READ_CHECKPOINTS(self.doc), [row])
		filters = self.get_all.call_args.kwargs["filters"]
		self.assertEqual(filters["run_id"], self.doc.last_run_id)
		self.assertEqual(filters["reference_name"], self.doc.name)
		self.assertEqual(filters["phase"], "Verifier Row")

	def test_run_button_state_follows_run_expiry(self):
		cases = [
			("Completed", datetime(2026, 10, 13, 12), True),
			("Running", datetime(2026, 10, 13, 11, 59), False),
			("Running", datetime(2026, 10, 12), True),
		]
		for status, started_on, can_run in cases:
			with self.subTest(status=status, started_on=started_on):
				self.doc.update(status=status, started_on=started_on, set_onload=Mock())
				verifier.ScannedDocumentAIVerifier.onload(self.doc)
				self.doc.set_onload.assert_called_once_with("can_run", can_run)

	def test_model_choice_is_the_first_route_for_columns_and_rows(self):
		self.doc.update(ai_account="local-account", model="local-model")
		self.vision.return_value = {"data": {"columns": self.value_columns}}
		verifier.decide_columns("Purchase Invoice", self.doc.prompt, route=verifier._route(self.doc))
		self.assertEqual(self.vision.call_args.kwargs["route"], ("local-account", "local-model"))
		self.get_all.return_value = ["SCAN-1"]
		self.vision.return_value = {"data": {"values": {"total_qty": 12}, "remarks": ""}}
		verifier._document_row(self.doc, "PI-1", self.columns)
		self.assertEqual(self.vision.call_args.kwargs["route"], ("local-account", "local-model"))

	def test_model_choice_needs_account_and_model(self):
		for account, model in (("local-account", None), (None, "local-model")):
			with self.subTest(account=account, model=model):
				self.doc.update(status="Completed", ai_account=account, model=model)
				with self.assertRaises(frappe.ValidationError):
					verifier.run_verifier(self.doc.name)
		self.enqueue.assert_not_called()

	def test_row_schema_is_strict_and_requires_every_column(self):
		schema = verifier._row_schema(self.value_columns)
		values = schema["properties"]["values"]
		self.assertEqual(values["required"], ["scan_weight", "total_qty", "difference"])
		self.assertFalse(values["additionalProperties"])
		self.assertFalse(schema["additionalProperties"])
		self.assertEqual(schema["required"], ["values", "remarks"])

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from frappe_tools.api import ocr_agent


class _Plugin:
	def validate(self, ctx, doc):
		return []


class TestOcrAgent(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def test_as_list_accepts_json(self):
		self.assertEqual(ocr_agent._as_list('["a", "b"]'), ["a", "b"])

	@patch("frappe_tools.api.ocr_agent.frappe.db.get_value")
	@patch("frappe_tools.api.ocr_agent.frappe.get_single")
	def test_processing_agents_respect_roles_and_expose_no_credentials(self, settings, get_value):
		settings.return_value = frappe._dict(vision_ai_model="My Extractor", decision_ai_model="My Verifier")
		get_value.side_effect = [
			frappe._dict(model_label="Luna", model_id="gpt-5.6-luna", enabled=1, api_key="must-not-leak"),
			frappe._dict(model_label="Sol", model_id="gpt-5.6-sol", enabled=1, api_key="must-not-leak"),
		]
		result = ocr_agent._processing_agents()
		self.assertEqual(result["extractor"]["name"], "My Extractor")
		self.assertEqual(result["verifier"]["model"], "gpt-5.6-sol")
		self.assertEqual(set(result["extractor"]), {"name", "label", "model", "enabled"})
		self.assertNotIn("must-not-leak", json.dumps(result))
		self.assertEqual(get_value.call_args.args[2], ["model_label", "model_id", "enabled"])

	@patch("frappe_tools.api.ocr_agent.frappe.db.get_value", return_value=None)
	@patch("frappe_tools.api.ocr_agent.frappe.get_single", return_value=frappe._dict())
	def test_missing_processing_agents_are_not_reported_enabled(self, _settings, _get_value):
		result = ocr_agent._processing_agents()
		self.assertEqual(result["extractor"]["name"], "Document Automation Luna")
		self.assertEqual(result["verifier"]["name"], "Document Verification Sol")
		self.assertFalse(result["verifier"]["enabled"])
		self.assertIsNone(result["extractor"]["model"])

	@patch("frappe.utils.background_jobs.get_job_status", return_value=None)
	def test_pending_record_with_missing_rq_job_is_stalled(self, _status):
		row = frappe._dict(status="Queued", decision_phase=None, processing_job_id="document-extraction::DOC-1")
		self.assertEqual(ocr_agent._job_state(row)["state"], "stale")

	@patch("frappe.utils.background_jobs.get_job_status")
	def test_redis_failure_is_not_misreported_as_stalled(self, status):
		status.side_effect = RuntimeError("redis unavailable")
		row = frappe._dict(status="Queued", decision_phase=None, processing_job_id="document-extraction::DOC-1")
		self.assertEqual(ocr_agent._job_state(row)["state"], "unavailable")

	@patch("frappe_tools.api.ocr_agent.llm.ensure_ready", side_effect=AssertionError("provider must not be checked"))
	def test_create_session_does_not_require_provider_credentials(self, ensure_ready):
		result = ocr_agent.create_session("Purchase Invoice")
		doc = frappe.get_doc("Document Extraction", result["extraction"])

		self.assertEqual(result["status"], "Draft")
		self.assertEqual(doc.target_doctype, "Purchase Invoice")
		ensure_ready.assert_not_called()

	@patch("frappe_tools.api.ocr_agent.automation_runtime.decide_and_stage")
	@patch("frappe_tools.api.ocr_agent.automation_runtime.enqueue_decision", return_value={"queued": True})
	@patch("frappe_tools.api.ocr_agent._job_state", return_value={"state": "not_applicable"})
	@patch("frappe_tools.api.ocr_agent._get_extraction")
	def test_manual_verifier_queues_without_calling_remote_model_in_http(self, get_extraction, _job, enqueue, direct):
		get_extraction.return_value = SimpleNamespace(name="DOCEXT-LOCAL", status="Review")
		self.assertEqual(ocr_agent.run_decision("DOCEXT-LOCAL"), {"queued": True})
		enqueue.assert_called_once_with("DOCEXT-LOCAL", enqueue_after_commit=True, force=True)
		direct.assert_not_called()

	def test_as_list_rejects_non_list(self):
		self.assertEqual(ocr_agent._as_list('{"a": 1}'), [])

	def test_as_list_rejects_invalid_json(self):
		self.assertEqual(ocr_agent._as_list("not-json"), [])

	@patch("frappe_tools.api.ocr_agent.frappe.get_all")
	@patch("frappe_tools.api.ocr_agent._get_extraction")
	def test_uploaded_files_are_scoped_to_the_capture_session(self, get_extraction, get_all):
		get_extraction.return_value = SimpleNamespace(name="DOCEXT-UPLOAD")
		get_all.return_value = [{"file_url": "/private/files/page.png", "content_hash": "abc"}]

		result = ocr_agent.get_uploaded_files("DOCEXT-UPLOAD")

		self.assertEqual(result[0]["content_hash"], "abc")
		self.assertEqual(
			get_all.call_args.kwargs["filters"]["attached_to_name"],
			"DOCEXT-UPLOAD",
		)
		get_extraction.assert_called_once_with("DOCEXT-UPLOAD", "read")

	def test_as_dict_accepts_json(self):
		self.assertEqual(ocr_agent._as_dict('{"a": 1}'), {"a": 1})

	def test_as_dict_rejects_invalid_json(self):
		self.assertEqual(ocr_agent._as_dict("not-json"), {})

	def test_json_object_is_safe_for_invalid_json(self):
		self.assertEqual(ocr_agent._json_object("not-json"), {})

	def test_json_object_rejects_json_arrays(self):
		self.assertEqual(ocr_agent._json_object('[{"a": 1}]'), {})

	def test_stages_report_extracting_as_active_read(self):
		stages = ocr_agent._stages("Extracting")
		self.assertEqual(stages[0]["state"], "done")
		self.assertEqual(stages[1]["state"], "done")
		self.assertEqual(stages[2]["state"], "done")
		self.assertEqual(stages[3]["state"], "active")
		self.assertEqual(stages[-1]["state"], "pending")

	def test_stages_report_queued_before_worker_starts(self):
		stages = ocr_agent._stages("Queued")
		self.assertEqual(stages[0]["state"], "done")
		self.assertEqual(stages[1]["state"], "done")
		self.assertEqual(stages[2]["state"], "done")
		self.assertEqual(stages[3]["state"], "active")

	def test_stages_report_classifying_before_extraction(self):
		stages = ocr_agent._stages("Classifying")
		self.assertEqual(stages[0]["state"], "done")
		self.assertEqual(stages[1]["state"], "done")
		self.assertEqual(stages[2]["state"], "active")
		self.assertEqual(stages[3]["state"], "pending")

	def test_stages_report_created_as_complete(self):
		self.assertTrue(all(stage["state"] == "done" for stage in ocr_agent._stages("Created")))

	def test_field_rows_keep_value_and_evidence(self):
		doc = SimpleNamespace(extracted_fields=[SimpleNamespace(
			fieldname="supplier",
			value="SUP-1",
			llm_value="Supplier One",
			llm_raw_text="Supplier One",
			confidence=0.91,
			status="Pending",
			source_page=2,
			bbox_json='{"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.1}',
			matched_value="SUP-1",
		)])
		rows = ocr_agent._field_rows(doc, [{
			"fieldname": "supplier", "label": "Supplier", "fieldtype": "Link", "required": True
		}])
		self.assertEqual(rows[0]["value"], "SUP-1")
		self.assertEqual(rows[0]["source_page"], 2)
		self.assertEqual(rows[0]["bbox"]["x"], 0.1)

	def test_field_rows_include_configured_missing_fields(self):
		rows = ocr_agent._field_rows(
			SimpleNamespace(extracted_fields=[]),
			[{"fieldname": "bill_no", "label": "Bill No", "fieldtype": "Data", "required": True}],
		)
		self.assertEqual(rows[0]["status"], "Missing")
		self.assertEqual(rows[0]["value"], "")

	def test_table_rows_keep_arbitrary_configured_values(self):
		line = SimpleNamespace(
			row_no=1, table="items", raw_json=json.dumps({"custom_code": "ABC", "qty": 2}),
			description="", supplier_code="", hsn="", qty=2, uom="", rate=0, amount=0,
			source_page=1, bbox_json="{}", resolution_status="Confirmed", match_confidence=1,
		)
		doc = SimpleNamespace(lines=[line], line_table="items")
		result = ocr_agent._table_rows(doc, [{
			"table": "items", "label": "Items", "columns": [{"key": "custom_code", "label": "Code"}]
		}])
		self.assertEqual(result[0]["rows"][0]["values"]["custom_code"], "ABC")

	def test_table_rows_expose_bounded_master_resolution_state(self):
		line = SimpleNamespace(
			row_no=1, table="items", raw_json='{"description":"Cotton"}',
			description="Cotton", supplier_code="", hsn="", qty=1, uom="Nos", rate=10, amount=10,
			source_page=1, bbox_json="{}", resolution_status="Matched", match_confidence=0.91,
			matched_item="ITEM-1", candidates_json='[{"value":"ITEM-1","label":"Cotton","score":0.91}]',
			master_proposal=None,
		)
		doc = SimpleNamespace(lines=[line], line_table="items")
		result = ocr_agent._table_rows(doc, [{
			"table": "items", "label": "Items", "columns": [{"key": "description", "label": "Description"}],
			"resolver": {"label": "Mapped Item", "link_doctype": "Item", "value_key": "item_code"},
		}])

		self.assertEqual(result[0]["resolver"]["link_doctype"], "Item")
		self.assertEqual(result[0]["rows"][0]["matched_value"], "ITEM-1")
		self.assertEqual(result[0]["rows"][0]["candidates"][0]["value"], "ITEM-1")

	def test_required_field_becomes_blocking_issue(self):
		fields = [{
			"fieldname": "supplier", "label": "Supplier", "required": True, "value": "",
			"status": "Missing", "confidence": 0, "source_page": 0, "bbox": None,
		}]
		issues = ocr_agent._review_issues(
			SimpleNamespace(status="Review"), fields, [], _Plugin(), None
		)
		self.assertEqual(issues[0]["severity"], "error")
		self.assertEqual(issues[0]["path"], "field:supplier")

	def test_rejected_required_field_remains_blocking(self):
		fields = [{
			"fieldname": "supplier", "label": "Supplier", "required": True, "value": "",
			"status": "Rejected", "confidence": 0, "source_page": 0, "bbox": None,
		}]
		issues = ocr_agent._review_issues(
			SimpleNamespace(status="Review"), fields, [], _Plugin(), None
		)
		self.assertEqual(issues[0]["severity"], "error")

	def test_low_confidence_field_becomes_warning(self):
		fields = [{
			"fieldname": "bill_no", "label": "Bill No", "required": False, "value": "A-1",
			"status": "Pending", "confidence": 0.4, "source_page": 1, "bbox": None,
		}]
		issues = ocr_agent._review_issues(
			SimpleNamespace(status="Review"), fields, [], _Plugin(), None
		)
		self.assertEqual(issues[0]["severity"], "warning")

	@patch("frappe_tools.api.ocr_agent.pipeline.processing_config", return_value={"require_verified_evidence": True})
	def test_unverified_model_box_blocks_creation(self, _config):
		fields = [{
			"fieldname": "bill_no", "label": "Bill No", "required": False, "value": "A-1",
			"status": "Pending", "confidence": 0.9, "source_page": 1,
			"bbox": {"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.04, "source": "model", "verified": False},
		}]
		issues = ocr_agent._review_issues(SimpleNamespace(status="Review"), fields, [], _Plugin(), None)
		self.assertTrue(any(issue["severity"] == "error" and "source highlight" in issue["message"] for issue in issues))

	@patch("frappe_tools.api.ocr_agent.pipeline.processing_config", return_value={"require_verified_evidence": True})
	def test_manual_verified_box_satisfies_evidence_gate(self, _config):
		fields = [{
			"fieldname": "bill_no", "label": "Bill No", "required": False, "value": "A-1",
			"status": "Edited", "confidence": 0.9, "source_page": 1,
			"bbox": {"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.04, "source": "manual", "verified": True},
		}]
		issues = ocr_agent._review_issues(SimpleNamespace(status="Review"), fields, [], _Plugin(), None)
		self.assertEqual(issues, [])

	@patch("frappe_tools.api.ocr_agent.frappe.db.exists", return_value=False)
	def test_unknown_link_becomes_blocking_issue(self, _exists):
		fields = [{
			"fieldname": "supplier", "label": "Supplier", "link_doctype": "Supplier",
			"required": True, "value": "Unknown", "status": "Pending", "confidence": 0.9,
			"source_page": 1, "bbox": None,
		}]
		issues = ocr_agent._review_issues(
			SimpleNamespace(status="Review"), fields, [], _Plugin(), None
		)
		self.assertTrue(any("existing Supplier" in issue["message"] for issue in issues))

	def test_line_arithmetic_mismatch_becomes_blocking_issue(self):
		tables = [{
			"fieldname": "items", "label": "Items", "columns": [],
			"rows": [{
				"row_no": 1, "values": {"qty": 2, "rate": 50, "amount": 90},
				"source_page": 1, "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.1, "source": "ocr", "verified": True},
			}],
		}]
		issues = ocr_agent._review_issues(
			SimpleNamespace(status="Review"), [], tables, _Plugin(), None
		)
		self.assertEqual(issues[0]["path"], "table:items:1:amount")

	def test_line_arithmetic_allows_small_rounding_difference(self):
		tables = [{
			"fieldname": "items", "label": "Items", "columns": [],
			"rows": [{
				"row_no": 1, "values": {"qty": 3, "rate": 33.333, "amount": 100},
				"status": "Confirmed",
				"source_page": 1, "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.1, "source": "ocr", "verified": True},
			}],
		}]
		issues = ocr_agent._review_issues(
			SimpleNamespace(status="Review"), [], tables, _Plugin(), None
		)
		self.assertEqual(issues, [])

	@patch("frappe_tools.api.ocr_agent.pipeline.processing_config", return_value={"require_verified_evidence": True})
	def test_row_box_does_not_verify_an_individual_cell(self, _required):
		tables = [{"fieldname": "items", "label": "Items", "columns": [{"key": "qty", "label": "Quantity", "evidence": True}],
			"rows": [{"row_no": 1, "values": {"qty": 2}, "status": "Confirmed", "source_page": 1,
				"bbox": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.1, "source": "manual", "verified": True}}]}]
		issues = ocr_agent._review_issues(SimpleNamespace(status="Review"), [], tables, _Plugin(), None)
		self.assertEqual(issues[0]["path"], "table:items:1:qty")
		self.assertEqual(issues[0]["column"], "qty")

	def test_action_preview_blocks_unreviewed_values(self):
		doc = SimpleNamespace(status="Review", target_doctype="Purchase Invoice")
		fields = [{"fieldname": "bill_no", "label": "Bill No", "value": "A-1", "status": "Pending"}]
		issues = [{"severity": "error", "message": "Confirm Bill No"}]
		preview = ocr_agent._action_preview(doc, fields, [], issues)
		self.assertFalse(preview["ready"])
		self.assertEqual(preview["field_count"], 0)
		self.assertFalse(preview["will_submit"])

	def test_action_preview_contains_only_accepted_mutations(self):
		doc = SimpleNamespace(status="Review", target_doctype="Purchase Invoice")
		fields = [
			{"fieldname": "bill_no", "label": "Bill No", "value": "A-1", "status": "Edited"},
			{"fieldname": "remarks", "label": "Remarks", "value": "guess", "status": "Pending"},
		]
		tables = [{"fieldname": "items", "label": "Items", "rows": [
			{"row_no": 1, "values": {"item_code": "ITEM-1"}, "status": "Confirmed"},
			{"row_no": 2, "values": {"item_code": "ITEM-2"}, "status": "Unmatched"},
		]}]
		preview = ocr_agent._action_preview(doc, fields, tables, [])
		self.assertTrue(preview["ready"])
		self.assertEqual(preview["field_count"], 1)
		self.assertEqual(preview["row_count"], 1)

	def test_required_table_value_becomes_blocking_issue(self):
		tables = [{
			"fieldname": "items", "label": "Items",
			"columns": [{"key": "description", "label": "Description", "required": True}],
			"rows": [{"row_no": 2, "values": {}, "source_page": 1, "bbox": None}],
		}]
		issues = ocr_agent._review_issues(
			SimpleNamespace(status="Review"), [], tables, _Plugin(), None
		)
		self.assertEqual(issues[0]["path"], "table:items:2:description")

	def test_plugin_validation_becomes_blocking_issue(self):
		class InvalidPlugin:
			def validate(self, ctx, doc):
				return ["Document-specific check failed"]

		issues = ocr_agent._review_issues(
			SimpleNamespace(status="Review"), [], [], InvalidPlugin(), None
		)
		self.assertEqual(issues[0]["message"], "Document-specific check failed")

	def test_issue_keeps_source_evidence(self):
		issue = ocr_agent._issue(
			"warning", "field:bill_no", "Check it",
			{"source_page": 2, "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}},
		)
		self.assertEqual(issue["source_page"], 2)
		self.assertEqual(issue["bbox"]["w"], 0.3)

	def test_mirror_common_line_values_updates_typed_columns(self):
		line = SimpleNamespace(description="", supplier_code="", hsn="", uom="", qty=0, rate=0, amount=0)
		ocr_agent._mirror_common_line_values(line, {
			"description": "Freight", "uom": "Nos", "qty": "2", "rate": "50.5", "amount": "101",
		})
		self.assertEqual(line.description, "Freight")
		self.assertEqual(line.qty, 2)
		self.assertEqual(line.rate, 50.5)

	@patch("frappe_tools.api.ocr_agent.get_run", return_value={"status": "Review"})
	@patch("frappe_tools.api.ocr_agent.get_plugin")
	@patch("frappe_tools.api.ocr_agent._get_extraction")
	def test_update_field_accepts_plugin_defined_virtual_schema(
		self, get_extraction, get_plugin, _get_run,
	):
		row = SimpleNamespace(
			fieldname="invoice_no", value="", status="Pending", edited_by=None,
			edited_on=None, matched_value=None, match_method=None,
		)
		doc = SimpleNamespace(
			name="DOCEXT-TEST", target_doctype="Return Goods", status="Review",
			extracted_fields=[row], save=Mock(),
		)
		get_extraction.return_value = doc
		get_plugin.return_value.schema.return_value = {
			"header": [{"fieldname": "invoice_no", "fieldtype": "Data"}],
			"tables": [],
		}

		ocr_agent.update_field("DOCEXT-TEST", "invoice_no", "INV-432")

		self.assertEqual(row.value, "INV-432")
		self.assertEqual(row.status, "Edited")
		doc.save.assert_called_once()

	@patch("frappe_tools.api.ocr_agent.get_run", return_value={"status": "Review"})
	@patch("frappe_tools.api.ocr_agent.get_plugin")
	@patch("frappe_tools.api.ocr_agent._get_extraction")
	def test_update_table_row_accepts_plugin_defined_virtual_table(
		self, get_extraction, get_plugin, _get_run,
	):
		line = SimpleNamespace(
			table="lines", row_no=1, raw_json="{}", description="", supplier_code="",
			hsn="", uom="", qty=0, rate=0, amount=0, resolution_status="Unmatched",
		)
		doc = SimpleNamespace(
			name="DOCEXT-TEST", target_doctype="Return Goods", status="Review",
			lines=[line], line_table="lines", save=Mock(),
		)
		get_extraction.return_value = doc
		get_plugin.return_value.schema.return_value = {
			"header": [],
			"tables": [{"table": "lines", "columns": [{"key": "description"}, {"key": "qty"}]}],
		}

		ocr_agent.update_table_row(
			"DOCEXT-TEST", "lines", 1, '{"description": "Cotton Yarn", "qty": 18}',
		)

		self.assertEqual(json.loads(line.raw_json)["description"], "Cotton Yarn")
		self.assertEqual(line.qty, 18)
		doc.save.assert_called_once()

	@patch("frappe_tools.api.ocr_agent.get_run", return_value={"status": "Review"})
	@patch("frappe_tools.api.ocr_agent._get_extraction")
	def test_update_bbox_persists_manual_field_region(self, get_extraction, _get_run):
		row = SimpleNamespace(
			fieldname="bill_no", source_page=1,
			bbox_json='{"x": 0.5, "y": 0.5, "w": 0.2, "h": 0.1}',
		)
		doc = SimpleNamespace(
			name="DOCEXT-TEST", status="Review", pages=[SimpleNamespace(page_no=1, width=1000, height=1400)], extracted_fields=[row],
			lines=[], line_table=None, save=Mock(),
		)
		get_extraction.return_value = doc
		ocr_agent.update_bbox(
			"DOCEXT-TEST", "field", 1,
			'{"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.04}',
			fieldname="bill_no",
		)
		box = json.loads(row.bbox_json)
		self.assertEqual(box["source"], "manual")
		self.assertTrue(box["verified"])
		self.assertEqual(box["x"], 0.1)
		self.assertEqual(box["model_bbox"]["x"], 0.5)
		doc.save.assert_called_once()

	@patch("frappe_tools.api.ocr_agent._get_extraction")
	def test_update_bbox_rejects_unknown_page(self, get_extraction):
		get_extraction.return_value = SimpleNamespace(
			status="Review", pages=[SimpleNamespace(page_no=1, width=1000, height=1400)], extracted_fields=[], lines=[]
		)
		with self.assertRaises(frappe.ValidationError):
			ocr_agent.update_bbox(
				"DOCEXT-TEST", "field", 9,
				'{"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.04}',
				fieldname="bill_no",
			)

	@patch("frappe_tools.api.ocr_agent.frappe.get_roles", return_value=["Scanner User"])
	def test_scanner_user_can_open_app(self, _roles):
		frappe.set_user("scanner@example.invalid")
		self.assertTrue(ocr_agent.has_app_permission())

	@patch("frappe_tools.api.ocr_agent.frappe.get_roles", return_value=["Employee"])
	def test_unrelated_user_cannot_open_app(self, _roles):
		frappe.set_user("employee@example.invalid")
		self.assertFalse(ocr_agent.has_app_permission())

	def test_guest_cannot_open_app(self):
		frappe.set_user("Guest")
		self.assertFalse(ocr_agent.has_app_permission())

	@patch("frappe_tools.api.ocr_agent.service.enqueue")
	@patch("frappe_tools.api.ocr_agent._verified_document_pages")
	@patch("frappe_tools.api.ocr_agent._get_extraction")
	def test_start_session_marks_queued_and_enqueues_by_page_count(
		self, get_extraction, verified_pages, enqueue,
	):
		doc = SimpleNamespace(
			name="DOCEXT-QUEUE", status="Draft", pages=[], error_log="old", save=Mock(),
		)
		doc.set = lambda field, value: setattr(doc, field, value)
		doc.append = lambda field, value: getattr(doc, field).append(frappe._dict(value))
		get_extraction.return_value = doc
		verified_pages.side_effect = [
			[{"page_no": 1, "image": "/private/files/p1.png", "file_name": "p1.png", "width": 1000, "height": 1400}],
			[{"page_no": 2, "image": "/private/files/p2.png", "file_name": "p2.png", "width": 1000, "height": 1400}],
		]
		enqueue.return_value = {
			"job_id": "document-extraction::DOCEXT-QUEUE", "queue": "long",
			"page_count": 2, "timeout_seconds": 360,
		}

		result = ocr_agent.start_session(
			"DOCEXT-QUEUE", '["/private/files/p1.png", "/private/files/p2.png"]',
		)

		self.assertEqual(doc.status, "Queued")
		self.assertEqual(len(doc.pages), 2)
		self.assertEqual(result["timeout_seconds"], 360)
		enqueue.assert_called_once_with(doc, enqueue_after_commit=True)

	def test_queue_status_groups_worker_and_review_states(self):
		self.assertEqual(ocr_agent._queue_status("Queued"), "pending")
		self.assertEqual(ocr_agent._queue_status("Extracting"), "pending")
		self.assertEqual(ocr_agent._queue_status("Review", "Running"), "pending")
		self.assertEqual(ocr_agent._queue_status("Review", "References Ready"), "ready")
		self.assertEqual(ocr_agent._queue_status("Review"), "ready")
		self.assertEqual(ocr_agent._queue_status("Review", "Human Handoff"), "ready")
		self.assertEqual(ocr_agent._queue_status("Created"), "validated")
		self.assertEqual(ocr_agent._queue_status("Failed"), "error")

	@patch("frappe.utils.file_manager.save_file")
	@patch("frappe_tools.api.ocr_agent.service.document_pages")
	@patch("frappe_tools.api.ocr_agent._verified_source_file")
	def test_uploaded_pdf_is_rendered_to_ordered_private_pages(self, source_file, document_pages, save_file):
		source_file.return_value = SimpleNamespace(
			file_name="invoice.pdf", get_content=Mock(return_value=b"%PDF-test")
		)
		document_pages.return_value = [
			{"content": b"page-1", "width": 1000, "height": 1400},
			{"content": b"page-2", "width": 1000, "height": 1400},
		]
		save_file.side_effect = [
			SimpleNamespace(file_url="/private/files/rendered-1.png"),
			SimpleNamespace(file_url="/private/files/rendered-2.png"),
		]

		pages = ocr_agent._verified_document_pages("DOCEXT-1", "/private/files/invoice.pdf", 3, 18)

		self.assertEqual([page["page_no"] for page in pages], [3, 4])
		self.assertEqual(pages[0]["image"], "/private/files/rendered-1.png")
		document_pages.assert_called_once_with(b"%PDF-test", ".pdf", "invoice.pdf", max_pages=18)

	@patch("frappe_tools.api.ocr_agent.frappe.has_permission")
	@patch("frappe_tools.api.ocr_agent._get_extraction")
	def test_create_draft_requires_review_status(self, get_extraction, _has_permission):
		get_extraction.return_value = SimpleNamespace(
			target_doctype="ToDo", status="Draft", created_document=None, operation_mode="Create New",
		)
		with self.assertRaises(frappe.ValidationError):
			ocr_agent.create_draft("DOCEXT-TEST")

	@patch("frappe_tools.api.ocr_agent.frappe.db.exists", return_value=True)
	@patch("frappe_tools.api.ocr_agent.frappe.db.sql")
	@patch("frappe_tools.api.ocr_agent.frappe.has_permission")
	@patch("frappe_tools.api.ocr_agent._get_extraction")
	def test_create_draft_retry_returns_existing_document(
		self, get_extraction, _has_permission, sql, _exists
	):
		get_extraction.return_value = SimpleNamespace(
			name="DOCEXT-TEST", target_doctype="ToDo", status="Created",
			created_document="TODO-EXISTING", operation_mode="Create New",
		)
		result = ocr_agent.create_draft("DOCEXT-TEST")
		self.assertTrue(result["already_created"])
		self.assertEqual(result["docname"], "TODO-EXISTING")
		sql.assert_not_called()

	@patch("frappe_tools.api.ocr_agent._review_issues")
	@patch("frappe_tools.api.ocr_agent._table_rows", return_value=[])
	@patch("frappe_tools.api.ocr_agent._field_rows", return_value=[])
	@patch("frappe_tools.api.ocr_agent.get_plugin")
	@patch("frappe_tools.api.ocr_agent.frappe.db.exists", return_value=False)
	@patch("frappe_tools.api.ocr_agent.frappe.db.sql")
	@patch("frappe_tools.api.ocr_agent.frappe.has_permission")
	@patch("frappe_tools.api.ocr_agent._get_extraction")
	def test_create_draft_blocks_deterministic_errors(
		self, get_extraction, _has_permission, sql, _exists, get_plugin,
		_fields, _tables, review_issues,
	):
		doc = SimpleNamespace(
			name="DOCEXT-TEST", target_doctype="ToDo", status="Review", created_document=None, operation_mode="Create New",
		)
		get_extraction.return_value = doc
		sql.return_value = [frappe._dict(created_document=None)]
		get_plugin.return_value.schema.return_value = {"header": [], "tables": []}
		review_issues.return_value = [{"severity": "error", "message": "Required value is missing"}]
		with self.assertRaises(frappe.ValidationError):
			ocr_agent.create_draft("DOCEXT-TEST")

	@patch("frappe_tools.api.ocr_agent.pipeline.build", return_value="TODO-NEW")
	@patch("frappe_tools.scan_lineage.finalize_extraction_target")
	@patch("frappe_tools.api.ocr_agent._review_issues", return_value=[])
	@patch("frappe_tools.api.ocr_agent._table_rows", return_value=[])
	@patch("frappe_tools.api.ocr_agent._field_rows", return_value=[])
	@patch("frappe_tools.api.ocr_agent.get_plugin")
	@patch("frappe_tools.api.ocr_agent.frappe.db.exists", return_value=False)
	@patch("frappe_tools.api.ocr_agent.frappe.db.sql")
	@patch("frappe_tools.api.ocr_agent.frappe.has_permission")
	@patch("frappe_tools.api.ocr_agent._get_extraction")
	def test_create_draft_builds_once_and_marks_extraction_created(
		self, get_extraction, _has_permission, sql, _exists, get_plugin,
		_fields, _tables, _issues, finalize, build,
	):
		doc = SimpleNamespace(
			name="DOCEXT-TEST", target_doctype="ToDo", status="Review", created_document=None,
			db_set=lambda *args, **kwargs: None, operation_mode="Create New",
		)
		get_extraction.return_value = doc
		sql.return_value = [frappe._dict(created_document=None)]
		get_plugin.return_value.schema.return_value = {"header": [], "tables": []}
		finalize.return_value = {
			"doctype": "ToDo", "docname": "TODO-NEW", "scanned_document": "DOC_SCAN-00001",
		}
		result = ocr_agent.create_draft("DOCEXT-TEST")
		build.assert_called_once_with("DOCEXT-TEST")
		finalize.assert_called_once_with(doc, "TODO-NEW", status="Created", stage="Target Document")
		self.assertFalse(result["already_created"])
		self.assertEqual(result["scanned_document"], "DOC_SCAN-00001")

	def test_non_review_status_has_no_review_issues(self):
		issues = ocr_agent._review_issues(
			SimpleNamespace(status="Extracting"), [], [], _Plugin(), None
		)
		self.assertEqual(issues, [])

	def test_get_run_returns_generic_review_view_model(self):
		doc = frappe.new_doc("Document Extraction")
		doc.target_doctype = "ToDo"
		doc.status = "Review"
		doc.append("extracted_fields", {
			"fieldname": "description",
			"label": "Description",
			"fieldtype": "Data",
			"value": "Call supplier",
			"llm_value": "Call supplier",
			"llm_raw_text": "Call supplier",
			"confidence": 0.96,
			"status": "Approved",
			"source_page": 1,
		})
		doc.insert(ignore_permissions=True)
		result = ocr_agent.get_run(doc.name)
		self.assertEqual(result["target_doctype"], "ToDo")
		self.assertEqual(result["status"], "Review")
		self.assertTrue(any(field["fieldname"] == "description" for field in result["fields"]))

	def test_create_draft_end_to_end_is_draft_and_idempotent(self):
		doc = frappe.new_doc("Document Extraction")
		doc.target_doctype = "ToDo"
		doc.status = "Review"
		doc.append("extracted_fields", {
			"fieldname": "description",
			"label": "Description",
			"fieldtype": "Data",
			"value": "OCR integration proof",
			"llm_value": "OCR integration proof",
			"llm_raw_text": "OCR integration proof",
			"confidence": 1,
			"status": "Approved",
			"source_page": 1,
			"bbox_json": json.dumps({
				"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.04,
				"source": "manual", "verified": True,
			}),
		})
		doc.insert(ignore_permissions=True)

		first = ocr_agent.create_draft(doc.name)
		created = frappe.get_doc("ToDo", first["docname"])
		self.assertEqual(created.docstatus, 0)
		self.assertEqual(created.description, "OCR integration proof")
		self.assertFalse(first["already_created"])

		second = ocr_agent.create_draft(doc.name)
		self.assertTrue(second["already_created"])
		self.assertEqual(second["docname"], first["docname"])

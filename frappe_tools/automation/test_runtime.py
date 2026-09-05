import json
import frappe
from types import SimpleNamespace
from unittest.mock import Mock, patch

from frappe.tests.utils import FrappeTestCase

from frappe_tools.automation.contracts import Decision, ReferenceBundle, ToolSpec
from frappe_tools.automation.learning import (
	_category,
	approved_memory,
	decision_value,
	memory_scope_keys,
	record_delta,
	scope_for_path,
)
from frappe_tools.automation import runtime
from frappe_tools.automation.runtime import _execute_tool
from frappe_tools.i2a import providers


class TestDocumentAutomationRuntime(FrappeTestCase):
	def setUp(self):
		super().setUp()
		# Unit tests may not start a real provider subprocess, including when a
		# capability probe switches to a newly added transport entry point.
		transport = patch("frappe_tools.automation.agent_providers.CodexAppServerClient", side_effect=AssertionError("Live provider transport is forbidden in unit tests"))
		transport.start()
		self.addCleanup(transport.stop)
		live = patch("frappe_tools.automation.runtime.agent_providers.supports_live_tools", return_value=False)
		self.live_capability = live.start()
		self.addCleanup(live.stop)

	@patch("frappe_tools.automation.runtime.prepare")
	@patch("frappe_tools.automation.runtime.agent_providers.call_with_live_tools")
	@patch("frappe_tools.automation.runtime.get_plugin")
	@patch("frappe_tools.automation.runtime.frappe.get_doc")
	def test_live_tool_transport_also_fails_closed(self, get_doc, get_plugin, call_live, _prepare):
		self.live_capability.return_value = True
		doc = SimpleNamespace(name="DOCEXT-LOCAL", status="Review", target_doctype="Purchase Invoice", pages=[], reload=Mock(), save=Mock())
		get_doc.side_effect = lambda doctype, name, **kwargs: frappe._dict(name=name, enabled=1, provider="Codex OAuth") if doctype == "AI Model" else doc
		get_plugin.return_value.collect_references.return_value = ReferenceBundle("ERPNext:Purchase Invoice", {}, {})
		get_plugin.return_value.decision_tools.return_value = []
		get_plugin.return_value.validate_decision.side_effect = lambda extraction, references, decision: decision
		call_live.side_effect = providers.ProviderError("synthetic unavailable")
		result = runtime.decide(doc.name, "Document Automation Luna")
		self.assertEqual(result["status"], "handoff")
		self.assertEqual(doc.decision_phase, "Human Handoff")
		call_live.assert_called_once()

	@patch("frappe_tools.utils.llm.call_vision", return_value={"data": {"observed_text": "INV-7"}})
	@patch("frappe_tools.extractors.pipeline.file_to_data_url", return_value="data:image/png;base64,AA==")
	def test_targeted_vision_reread_is_read_only_and_limited_to_one_call(self, file_to_data_url, call_vision):
		extraction = SimpleNamespace(
			name="DOCEXT-1", target_doctype="LR Processing",
			pages=[SimpleNamespace(page_no=1, image="/private/files/lr.png")],
		)
		budget = {"remaining": 1}
		first = runtime._targeted_vision_reread(extraction, 1, "table:invoice_references:1", budget)
		second = runtime._targeted_vision_reread(extraction, 1, "table:invoice_references:1", budget)

		self.assertEqual(first["observation"]["observed_text"], "INV-7")
		self.assertIn("budget exhausted", second["error"])
		file_to_data_url.assert_called_once_with("/private/files/lr.png")
		call_vision.assert_called_once()
	@patch("frappe_tools.automation.runtime.apply", return_value={"ok": True, "target_created": False})
	@patch("frappe_tools.automation.runtime.decide", return_value={"status": "review", "confidence": 0.9})
	def test_decide_and_stage_applies_only_review_ready_result(self, decide, apply):
		result = runtime.decide_and_stage("DOCEXT-1", "Document Automation Luna")

		self.assertTrue(result["staging"]["ok"])
		decide.assert_called_once_with("DOCEXT-1", "Document Automation Luna")
		apply.assert_called_once_with("DOCEXT-1")

	@patch("frappe_tools.automation.runtime.apply")
	@patch("frappe_tools.automation.runtime.decide", return_value={"status": "handoff", "confidence": 0.2})
	def test_decide_and_stage_never_applies_handoff(self, _decide, apply):
		result = runtime.decide_and_stage("DOCEXT-1", "Document Automation Luna")

		self.assertEqual(result["status"], "handoff")
		apply.assert_not_called()

	@patch("frappe_tools.automation.runtime.frappe.enqueue")
	@patch("frappe_tools.automation.runtime.get_plugin")
	@patch("frappe_tools.automation.runtime.frappe.get_doc")
	def test_automatic_decision_has_independent_deduplicated_job(self, get_doc, get_plugin, enqueue):
		extraction = SimpleNamespace(
			name="DOCEXT-1", status="Review", target_doctype="Purchase Invoice", decision_phase=None,
			decision_job_id=None, save=Mock(), get=lambda key, default=None: 1 if key == "auto_decide" else default,
		)
		model = frappe._dict(name="Document Automation Luna", enabled=1, provider="Codex OAuth", agent_timeout_seconds=60)
		get_doc.side_effect = lambda doctype, name: extraction if doctype == "Document Extraction" else model
		get_plugin.return_value.decision_model_name.return_value = "Document Automation Luna"

		result = runtime.enqueue_decision("DOCEXT-1", enqueue_after_commit=True)

		self.assertTrue(result["queued"])
		self.assertEqual(extraction.decision_phase, "Queued")
		self.assertEqual(extraction.decision_job_id, "document-decision::DOCEXT-1")
		self.assertTrue(enqueue.call_args.kwargs["deduplicate"])
		self.assertEqual(enqueue.call_args.kwargs["job_id"], "document-decision::DOCEXT-1")

	@patch("frappe_tools.utils.llm.call_vision")
	@patch("frappe_tools.automation.runtime.enqueue_decision", return_value={"queued": True})
	@patch("frappe_tools.automation.runtime.prepare", return_value={"adapter_id": "ERPNext:Purchase Invoice"})
	@patch("frappe_tools.automation.runtime.frappe.get_doc")
	def test_reprocess_reuses_stored_extraction_without_vision(self, get_doc, prepare, enqueue, call_vision):
		extraction = SimpleNamespace(
			name="DOCEXT-1", status="Review", reference_bundle_json="old", decision_json="old",
			tool_trace_json="old", handoff_reason="missing HSN", decision_phase="Human Handoff",
			flags=SimpleNamespace(), save=Mock(), add_processing_event=Mock(),
		)
		get_doc.return_value = extraction

		result = runtime.reprocess("DOCEXT-1")

		self.assertTrue(result["queued"])
		prepare.assert_called_once_with("DOCEXT-1")
		enqueue.assert_called_once_with("DOCEXT-1", enqueue_after_commit=True, force=True)
		call_vision.assert_not_called()
		self.assertIsNone(extraction.decision_json)

	def test_invalid_agent_status_fails_closed_to_handoff(self):
		decision = Decision.from_dict({"status": "auto_create", "confidence": 4, "values": {"item": "X"}})
		self.assertEqual(decision.status, "handoff")
		self.assertEqual(decision.confidence, 1)

	def test_agent_cannot_execute_write_tool(self):
		tool = ToolSpec("danger", "", {"type": "object"}, lambda: {"mutated": True}, kind="write")
		self.assertIn("read tools only", _execute_tool(tool, {})["error"])

	def test_undeclared_arguments_are_dropped(self):
		def handler(query=None):
			return {"query": query}
		tool = ToolSpec("search", "", {"type": "object", "properties": {"query": {"type": "string"}}}, handler)
		with patch("frappe_tools.automation.runtime.frappe.has_permission", return_value=True):
			self.assertEqual(_execute_tool(tool, {"query": "vest", "doctype": "User"}), {"query": "vest"})

	def test_review_delta_is_three_way_audit_input(self):
		doc = SimpleNamespace(review_delta_json=None, decision_json=None)
		record_delta(doc, "table:items:1:item_code", "printed vest", "ITEM-001", "candidate ITEM-002")
		self.assertIn("ITEM-001", doc.review_delta_json)
		self.assertEqual(_category("table:items:1:item_code"), "Identity")

	def test_repeated_review_edit_keeps_original_model_value_and_latest_human_value(self):
		doc = SimpleNamespace(review_delta_json=None)
		record_delta(doc, "field:supplier", "Printed Supplier", "SUP-1", "SUP-2")
		record_delta(doc, "field:supplier", "SUP-1", "SUP-FINAL")
		deltas = json.loads(doc.review_delta_json)

		self.assertEqual(len(deltas), 1)
		self.assertEqual(deltas[0]["model_value"], "Printed Supplier")
		self.assertEqual(deltas[0]["agent_value"], "SUP-2")
		self.assertEqual(deltas[0]["human_value"], "SUP-FINAL")

	def test_decision_value_resolves_header_and_table_paths(self):
		doc = SimpleNamespace(decision_json='{"values":{"supplier":"SUP-2","items":[{"row_no":1,"item_code":"ITEM-2"}]}}')

		self.assertEqual(decision_value(doc, "field:supplier"), "SUP-2")
		self.assertEqual(decision_value(doc, "table:items:1:item_code"), "ITEM-2")
		self.assertIsNone(decision_value(doc, "table:items:2:item_code"))

	def test_table_correction_uses_document_category_scope(self):
		doc = SimpleNamespace(
			extracted_fields=[], line_table="items",
			lines=[SimpleNamespace(row_no=1, table="items", raw_json='{"document_category":"Vendor Blue Layout"}')],
		)

		self.assertEqual(scope_for_path(doc, "table:items:1:item_code"), "category:vendor-blue-layout")

	def test_lr_header_category_is_available_to_scoped_memory(self):
		doc = SimpleNamespace(
			extracted_fields=[SimpleNamespace(
				fieldname="document_category", value="Delhivery Surface", llm_value="Delhivery Surface",
			)],
			lines=[],
		)

		self.assertIn("category:delhivery-surface", memory_scope_keys(doc))

	@patch("frappe_tools.automation.learning.frappe.get_all")
	def test_only_repeated_non_conflicting_approved_memory_becomes_active(self, get_all):
		get_all.return_value = [
			{"name": "DMEM-1", "path": "field:supplier", "scope_key": "category:vendor-blue", "human_value": '"SUP-1"'},
			{"name": "DMEM-2", "path": "field:supplier", "scope_key": "category:vendor-blue", "human_value": '"SUP-1"'},
			{"name": "DMEM-3", "path": "field:company", "scope_key": "global", "human_value": '"CO-1"'},
		]

		result = approved_memory("ERPNext:Purchase Invoice", ["global", "category:vendor-blue"])

		self.assertEqual(result["active"][0]["value"], "SUP-1")
		self.assertEqual(result["active"][0]["confirmations"], 2)
		self.assertEqual(len(result["active"]), 1)

	@patch("frappe_tools.automation.learning.frappe.get_all")
	def test_conflicting_approved_memory_is_never_active(self, get_all):
		get_all.return_value = [
			{"name": "DMEM-1", "path": "field:supplier", "scope_key": "global", "human_value": '"SUP-1"'},
			{"name": "DMEM-2", "path": "field:supplier", "scope_key": "global", "human_value": '"SUP-2"'},
		]

		result = approved_memory("ERPNext:Purchase Invoice")

		self.assertEqual(result["active"], [])
		self.assertEqual(result["conflicts"][0]["path"], "field:supplier")

	@patch("frappe_tools.automation.runtime.prepare")
	@patch("frappe_tools.automation.runtime.agent_providers.call_with_tools")
	@patch("frappe_tools.automation.runtime.get_plugin")
	@patch("frappe_tools.automation.runtime.frappe.get_doc")
	def test_provider_failure_is_persisted_as_human_handoff(self, get_doc, get_plugin, call_with_tools, _prepare):
		doc = SimpleNamespace(
			name="DOCEXT-1", status="Review", target_doctype="Purchase Invoice", decision_json=None,
			tool_trace_json=None, handoff_reason=None, decision_phase=None, reload=Mock(), save=Mock(),
		)
		bundle = ReferenceBundle("ERPNext:Purchase Invoice", {"fields": {}}, {})
		plugin = get_plugin.return_value
		plugin.collect_references.return_value = bundle
		plugin.decision_tools.return_value = []
		plugin.decision_instructions.return_value = "Use local evidence only"
		plugin.adapter_id.return_value = "ERPNext:Purchase Invoice"
		plugin.validate_decision.side_effect = lambda extraction, references, decision: decision
		get_doc.side_effect = lambda doctype, name, **kwargs: frappe._dict(name=name, enabled=1, provider="Codex OAuth") if doctype == "AI Model" else doc
		call_with_tools.side_effect = providers.ProviderError("model unavailable")

		result = runtime.decide("DOCEXT-1", "Document Automation Luna")

		self.assertEqual(result["status"], "handoff")
		self.assertEqual(doc.decision_phase, "Human Handoff")
		self.assertIn("agent_unavailable", doc.handoff_reason)
		doc.save.assert_called_once_with(ignore_permissions=True)

	@patch("frappe_tools.automation.runtime.frappe.has_permission", return_value=True)
	@patch("frappe_tools.automation.runtime.prepare")
	@patch("frappe_tools.automation.runtime.agent_providers.call_with_tools")
	@patch("frappe_tools.automation.runtime.get_plugin")
	@patch("frappe_tools.automation.runtime.frappe.get_doc")
	def test_bounded_read_tool_round_finishes_review_ready(
		self, get_doc, get_plugin, call_with_tools, _prepare, _permission,
	):
		doc = SimpleNamespace(
			name="DOCEXT-2", status="Review", target_doctype="Purchase Invoice", decision_json=None,
			tool_trace_json=None, handoff_reason=None, decision_phase=None, reload=Mock(), save=Mock(),
		)
		bundle = ReferenceBundle("ERPNext:Purchase Invoice", {"fields": {}}, {"supplier": []})
		tool = ToolSpec(
			"search_suppliers", "Search local suppliers", {"type": "object", "properties": {"query": {"type": "string"}}},
			lambda query=None: {"candidates": [{"value": "SUP-1"}]}, permission_doctype="Supplier",
		)
		plugin = get_plugin.return_value
		plugin.collect_references.return_value = bundle
		plugin.decision_tools.return_value = [tool]
		plugin.decision_instructions.return_value = "Use local evidence only"
		plugin.adapter_id.return_value = "ERPNext:Purchase Invoice"
		plugin.validate_decision.side_effect = lambda extraction, references, decision: decision
		get_doc.side_effect = lambda doctype, name, **kwargs: frappe._dict(name=name, enabled=1, provider="Codex OAuth") if doctype == "AI Model" else doc
		call_with_tools.side_effect = [
			{"message": {"role": "assistant", "content": "", "tool_calls": []},
			 "tool_calls": [{"id": "call-1", "name": "search_suppliers", "arguments": {"query": "Supplier"}}]},
			{"message": {"role": "assistant", "content": '{"status":"review","confidence":0.98,"values":{"supplier":"SUP-1"},"reasons":["exact local match"]}'},
			 "content": '{"status":"review","confidence":0.98,"values":{"supplier":"SUP-1"},"reasons":["exact local match"]}', "tool_calls": []},
		]

		result = runtime.decide("DOCEXT-2", "Document Automation Luna")

		self.assertEqual(result["status"], "review")
		self.assertEqual(doc.decision_phase, "Review Ready")
		self.assertIn("search_suppliers", doc.tool_trace_json)
		self.assertEqual(call_with_tools.call_count, 2)

	@patch("frappe_tools.automation.runtime.prepare")
	@patch("frappe_tools.automation.runtime.agent_providers.call_with_tools")
	@patch("frappe_tools.automation.runtime.get_plugin")
	@patch("frappe_tools.automation.runtime.frappe.get_doc")
	def test_low_confidence_agent_review_fails_closed(self, get_doc, get_plugin, call_with_tools, _prepare):
		doc = SimpleNamespace(
			name="DOCEXT-3", status="Review", target_doctype="Purchase Invoice", decision_json=None,
			tool_trace_json=None, handoff_reason=None, decision_phase=None, reload=Mock(), save=Mock(),
		)
		bundle = ReferenceBundle("ERPNext:Purchase Invoice", {"fields": {}}, {})
		plugin = get_plugin.return_value
		plugin.collect_references.return_value = bundle
		plugin.decision_tools.return_value = []
		plugin.decision_instructions.return_value = "Use local evidence only"
		plugin.adapter_id.return_value = "ERPNext:Purchase Invoice"
		plugin.validate_decision.side_effect = lambda extraction, references, decision: decision
		get_doc.side_effect = lambda doctype, name, **kwargs: frappe._dict(name=name, enabled=1, provider="Codex OAuth") if doctype == "AI Model" else doc
		call_with_tools.return_value = {
			"message": {"role": "assistant", "content": ""},
			"content": json.dumps({
				"status": "review", "confidence": 0.60, "values": {}, "reasons": ["weak evidence"],
			}),
			"tool_calls": [],
		}

		result = runtime.decide("DOCEXT-3", "Document Automation Luna")

		self.assertEqual(result["status"], "handoff")
		self.assertIn("decision_confidence_below_0.85", result["handoff_reasons"])

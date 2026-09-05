"""Bounded agent↔code loop over adapter-owned read tools."""

import inspect
import json

import frappe

from frappe_tools.automation.contracts import Decision, ReferenceBundle, ToolSpec
from frappe_tools.automation import agent_providers
from frappe_tools.automation import revisions
from frappe_tools.extractors import get_plugin
from frappe_tools.i2a import providers
from frappe_tools.utils.llm import safe_json_loads

MAX_TOOL_ROUNDS = 6
MIN_REVIEW_CONFIDENCE = 0.85


def enqueue_decision(extraction_name, *, enqueue_after_commit=False, force=False):
	"""Queue one idempotent decision job after vision/reference extraction."""
	extraction = frappe.get_doc("Document Extraction", extraction_name)
	if extraction.status != "Review":
		return {"extraction": extraction.name, "queued": False, "reason": "not_in_review"}
	if not extraction.get("auto_decide") and not force:
		return {"extraction": extraction.name, "queued": False, "reason": "automatic_decision_disabled"}
	plugin = get_plugin(extraction.target_doctype)
	model_name = plugin.decision_model_name()
	model = frappe.get_doc("AI Model", model_name)
	_validate_document_agent_model(model)
	turn_timeout = int(model.get("agent_timeout_seconds") or 300)
	queue_wait = int(model.get("agent_queue_timeout_seconds") or 30)
	timeout = max((turn_timeout + queue_wait) * MAX_TOOL_ROUNDS + 60, 300)
	job_id = f"document-decision::{extraction.name}"
	extraction.decision_phase = "Queued"
	extraction.decision_job_id = job_id
	_record_event(extraction, "Agent Decision", "Queued", "Queued the configured subscription-agent decision job.", {
		"model": model_name,
	})
	_skip_auto_process(extraction)
	extraction.save(ignore_permissions=True)
	frappe.enqueue(
		"frappe_tools.automation.runtime.run_queued_decision",
		queue="long",
		timeout=timeout,
		extraction_name=extraction.name,
		enqueue_after_commit=enqueue_after_commit,
		job_id=job_id,
		deduplicate=True,
	)
	return {"extraction": extraction.name, "queued": True, "job_id": job_id, "model": model_name, "timeout": timeout}


def run_queued_decision(extraction_name):
	"""Background boundary: decision errors preserve OCR data as human handoff."""
	extraction = frappe.get_doc("Document Extraction", extraction_name)
	if extraction.status != "Review":
		return {"extraction": extraction.name, "status": extraction.status, "skipped": True}
	try:
		extraction.decision_phase = "Running"
		_record_event(extraction, "Agent Decision", "Running", "Subscription-agent reasoning started.")
		_skip_auto_process(extraction)
		extraction.save(ignore_permissions=True)
		frappe.db.commit()
		result = decide_and_stage(extraction.name, get_plugin(extraction.target_doctype).decision_model_name())
		frappe.db.commit()
		return {"extraction": extraction.name, **result}
	except Exception as exc:
		frappe.db.rollback()
		failed = frappe.get_doc("Document Extraction", extraction_name)
		failed.decision_phase = "Human Handoff"
		failed.handoff_reason = f"agent_runtime_error: {str(exc)[:500]}"
		_record_event(failed, "Agent Decision", "Human Handoff", str(exc)[:500])
		_skip_auto_process(failed)
		failed.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.log_error(frappe.get_traceback(), f"Document decision failed: {extraction_name}")
		return {"extraction": failed.name, "status": "handoff", "error": str(exc)[:500]}


def prepare(extraction_name):
	extraction = frappe.get_doc("Document Extraction", extraction_name)
	plugin = get_plugin(extraction.target_doctype)
	bundle = _reference_bundle(plugin, extraction)
	if not isinstance(bundle, ReferenceBundle):
		raise TypeError("adapter.reference must return ReferenceBundle")
	extraction.adapter_id = plugin.adapter_id()
	extraction.reference_bundle_json = json.dumps(bundle.as_dict(), default=str, ensure_ascii=False)
	extraction.decision_phase = "References Ready"
	_record_event(extraction, "Reference Resolution", "References Ready", "Collected bounded local references for the target adapter.", {
		"adapter": plugin.adapter_id(),
		"warnings": bundle.warnings,
	})
	_skip_auto_process(extraction)
	extraction.save(ignore_permissions=True)
	return bundle.as_dict()


def decide(extraction_name, model_name):
	"""Run the text agent on bounded local evidence. It cannot mutate Frappe."""
	extraction = frappe.get_doc("Document Extraction", extraction_name)
	if extraction.status != "Review":
		frappe.throw("Decision matching requires a completed extraction.")
	plugin = get_plugin(extraction.target_doctype)
	_validate_document_agent_model(frappe.get_doc("AI Model", model_name))
	input_fingerprint = revisions.fingerprint(extraction)
	# Do not save/lock the review while waiting for a remote model. A user may
	# correct the document meanwhile; its response is checked under lock below.
	bundle = _reference_bundle(plugin, extraction)
	tools = {tool.name: tool for tool in plugin.decision_tools(extraction, bundle)}
	vision_budget = {"remaining": 1}
	if getattr(extraction, "pages", None):
		tools["reread_document_evidence"] = ToolSpec(
			"reread_document_evidence",
			"Reread one document page once when a specific extracted field or row is visibly ambiguous.",
			{
				"type": "object",
				"properties": {
					"page_no": {"type": "integer", "minimum": 1},
					"evidence_path": {"type": "string", "maxLength": 120},
				},
				"required": ["page_no", "evidence_path"],
				"additionalProperties": False,
			},
			lambda page_no, evidence_path: _targeted_vision_reread(
				extraction, page_no, evidence_path, vision_budget
			),
		)
	messages = [{"role": "system", "content": plugin.decision_instructions()}, {"role": "user", "content":
		"Facts, bounded references and memory:\n" + json.dumps(bundle.as_dict(), default=str, ensure_ascii=False)}]
	trace, decision = [], None
	tool_specs = [tool.function_spec() for tool in tools.values()]
	if agent_providers.supports_live_tools(model_name):
		try:
			response = agent_providers.call_with_live_tools(
				model_name,
				messages,
				tool_specs,
				lambda name, arguments: _execute_tool(tools.get(name), arguments),
				purpose="document_decision",
				run=extraction.name,
				action=plugin.adapter_id(),
			)
			trace.extend(response.get("tool_trace") or [])
			trace.append({
				"round": 1,
				"type": "native_agent_response",
				"provider": response.get("transport_provider"),
				"model": response.get("transport_model"),
				"finish_reason": response.get("finish_reason"),
				"agent_thread_id": response.get("agent_thread_id"),
			})
			decision = Decision.from_dict(safe_json_loads(response.get("content") or "{}"))
		except providers.ProviderError as exc:
			trace.append({"round": 1, "error": str(exc)[:300]})
			decision = Decision("handoff", 0, {}, ["Decision agent is unavailable"], ["agent_unavailable"])
	else:
		for round_no in range(1, MAX_TOOL_ROUNDS + 1):
			try:
				response = agent_providers.call_with_tools(model_name, messages, tool_specs,
					purpose="document_decision", run=extraction.name, action=plugin.adapter_id())
			except providers.ProviderError as exc:
				trace.append({"round": round_no, "error": str(exc)[:300]})
				decision = Decision("handoff", 0, {}, ["Decision agent is unavailable"], ["agent_unavailable"])
				break
			messages.append(response.get("message") or {"role": "assistant", "content": response.get("content") or ""})
			trace.append({"round": round_no, "type": "agent_response",
				"provider": response.get("transport_provider"), "model": response.get("transport_model"),
				"finish_reason": response.get("finish_reason")})
			calls = response.get("tool_calls") or []
			if not calls:
				decision = Decision.from_dict(safe_json_loads(response.get("content") or "{}"))
				break
			for call in calls:
				result = _execute_tool(tools.get(call.get("name")), call.get("arguments") or {})
				trace.append({"round": round_no, "tool": call.get("name"), "arguments": call.get("arguments") or {}, "result": result})
				messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, default=str)[:8000]})
	if decision is None:
		decision = Decision("handoff", 0, {}, ["Agent did not finish inside the bounded tool budget"], ["decision_round_limit"])
	extraction = frappe.get_doc("Document Extraction", extraction_name, for_update=True)
	if extraction.status != "Review" or revisions.fingerprint(extraction) != input_fingerprint:
		_record_event(extraction, "Agent Decision", "Stale", "Discarded an agent response because the review changed while matching ran.")
		if extraction.status == "Review":
			extraction.decision_phase = "Stale"
			extraction.handoff_reason = revisions.STALE_MESSAGE
		_skip_auto_process(extraction)
		extraction.save(ignore_permissions=True)
		return Decision("handoff", 0, {}, [], [revisions.STALE_MESSAGE]).as_dict()
	validated = plugin.validate_decision(extraction, bundle, decision)
	if validated.status == "review" and validated.confidence < MIN_REVIEW_CONFIDENCE:
		validated.status = "handoff"
		validated.handoff_reasons = list(dict.fromkeys([
			*validated.handoff_reasons,
			f"decision_confidence_below_{MIN_REVIEW_CONFIDENCE:.2f}",
		]))
	revisions.bind(extraction, validated.as_dict(), input_fingerprint=input_fingerprint)
	extraction.adapter_id = plugin.adapter_id()
	extraction.reference_bundle_json = json.dumps(bundle.as_dict(), default=str, ensure_ascii=False)
	extraction.tool_trace_json = json.dumps(trace, default=str, ensure_ascii=False)
	extraction.handoff_reason = "\n".join(validated.handoff_reasons)
	extraction.decision_phase = "Human Handoff" if validated.status == "handoff" else "Review Ready"
	_record_event(extraction, "Agent Decision", extraction.decision_phase,
		"Decision requires human handoff." if validated.status == "handoff" else "Validated decision is ready for review.", {
			"confidence": validated.confidence,
			"reasons": validated.handoff_reasons,
			"tool_rounds": len(trace),
		})
	_skip_auto_process(extraction)
	with revisions.decision_write():
		extraction.save(ignore_permissions=True)
	return validated.as_dict()


def _validate_document_agent_model(model):
	"""Document decisions are Codex OAuth only and always fail closed."""
	if not model.enabled:
		frappe.throw(f"Document decision model {model.name} is disabled.")
	if model.provider != "Codex OAuth":
		frappe.throw("Document decisions only permit a Codex OAuth AI Model.")
	if model.get("fallback_model"):
		frappe.throw("Document decisions do not permit fallback models.")


def reprocess(extraction_name, *, enqueue_after_commit=True):
	"""Re-run code references + decision agent from persisted extraction only.

	No source preparation, page routing, OCR or vision function is reachable from
	this entry point.  This is the safe retry after a master-data handoff.
	"""
	extraction = frappe.get_doc("Document Extraction", extraction_name)
	if extraction.status != "Review":
		frappe.throw("Decision reprocessing requires a completed extraction.")
	extraction.reference_bundle_json = None
	extraction.decision_json = None
	extraction.tool_trace_json = None
	extraction.handoff_reason = None
	extraction.decision_phase = None
	_record_event(
		extraction,
		"Agent Decision",
		"Reprocessing",
		"Reusing stored OCR extraction; vision was not called.",
	)
	_skip_auto_process(extraction)
	with revisions.decision_write():
		extraction.save(ignore_permissions=True)
	prepare(extraction.name)
	return enqueue_decision(extraction.name, enqueue_after_commit=enqueue_after_commit, force=True)


def decide_and_stage(extraction_name, model_name):
	"""Complete agent reasoning and copy a valid choice into review staging."""
	decision = decide(extraction_name, model_name)
	if decision.get("status") != "review":
		return decision
	staging = apply(extraction_name)
	return {**decision, "staging": staging}


def apply(extraction_name):
	"""Apply an already validated agent decision to review staging, never to the target DocType."""
	extraction = frappe.get_doc("Document Extraction", extraction_name, for_update=True)
	plugin = get_plugin(extraction.target_doctype)
	decision = Decision.from_dict(safe_json_loads(extraction.decision_json or "{}"))
	if extraction.status != "Review" or extraction.decision_phase != "Review Ready" or decision.status != "review":
		frappe.throw("Only a validated review-ready decision can be applied.")
	if not revisions.is_current(extraction):
		frappe.throw(revisions.STALE_MESSAGE)
	decision = plugin.validate_decision(extraction, _reference_bundle(plugin, extraction), decision)
	if decision.status != "review" or decision.confidence < MIN_REVIEW_CONFIDENCE:
		frappe.throw("The selected records are no longer valid. Run matching again.")
	input_fingerprint = revisions.fingerprint(extraction)
	with revisions.decision_write():
		result = plugin.apply_decision(extraction, decision)
		extraction.reload()
	revisions.bind(extraction, decision.as_dict(), input_fingerprint=input_fingerprint, applied=True)
	extraction.decision_phase = "Applied to Review"
	_record_event(
		extraction,
		"Review Staging",
		"Applied to Review",
		"Validated agent choices were applied to review staging; no target was created.",
	)
	_skip_auto_process(extraction)
	with revisions.decision_write():
		extraction.save(ignore_permissions=True)
	return result


def _execute_tool(tool, arguments):
	if not tool:
		return {"error": "unknown tool"}
	if tool.kind != "read":
		return {"error": "agent runtime permits read tools only"}
	if tool.permission_doctype and not frappe.has_permission(tool.permission_doctype, "read"):
		return {"error": f"no read permission for {tool.permission_doctype}"}
	allowed = set(inspect.signature(tool.handler).parameters)
	args = {key: value for key, value in arguments.items() if key in allowed}
	try:
		return json.loads(json.dumps(tool.handler(**args), default=str))
	except Exception as exc:
		return {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}


def _targeted_vision_reread(extraction, page_no, evidence_path, budget):
	"""One bounded, read-only evidence refresh requested by the decision agent."""
	if budget.get("remaining", 0) <= 0:
		return {"error": "targeted vision reread budget exhausted"}
	path = str(evidence_path or "").strip()
	if len(path) > 120 or not (path.startswith("field:") or path.startswith("table:")):
		return {"error": "evidence_path must identify one configured field or table row"}
	page = next((row for row in extraction.pages if int(row.page_no or 0) == int(page_no or 0)), None)
	if not page or not page.image:
		return {"error": "requested page is not part of this document"}
	budget["remaining"] = 0
	from frappe_tools.extractors.pipeline import file_to_data_url
	from frappe_tools.utils import llm

	result = llm.call_vision(
		[file_to_data_url(page.image)],
		"Reread only the requested visible evidence. Return observed_text, normalized_value, confidence, and bbox "
		"with normalized x/y/w/h coordinates. Never infer a value not visibly present.",
		f"Evidence path: {path}. Page number: {int(page_no)}. Return one JSON object for this evidence only.",
		extraction=extraction.name,
		target_doctype=extraction.target_doctype,
	)
	return {"page_no": int(page_no), "evidence_path": path, "observation": result.get("data") or {}}


def _reference_bundle(plugin, extraction):
	"""Call the v1 dict adapter, retaining compatibility with older plugins."""
	arguments = plugin.reference_arguments(extraction) if hasattr(plugin, "reference_arguments") else {}
	bundle = plugin.reference(extraction, arguments) if hasattr(plugin, "reference") else None
	if not isinstance(bundle, ReferenceBundle) and hasattr(plugin, "collect_references"):
		bundle = plugin.collect_references(extraction)
	return bundle


def _record_event(extraction, stage, status, message, details=None):
	"""Keep runtime unit doubles compatible while persisting events on real runs."""
	if hasattr(extraction, "add_processing_event"):
		extraction.add_processing_event(stage, status, message, details)


def _skip_auto_process(extraction):
	if hasattr(extraction, "flags"):
		extraction.flags.skip_auto_process = True

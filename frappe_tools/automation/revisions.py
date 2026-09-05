"""Bind a server-validated decision to the exact review data it evaluated.

Acknowledging unchanged data is not a new input. Changing a value, mapping,
rejection, source page or evidence is. Old decisions remain available for audit,
but cannot authorize writes after those inputs change.
"""

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar

import frappe


_trusted_write = ContextVar("document_decision_write", default=False)
STALE_MESSAGE = "Review data changed. Run matching again before creating the document."


@contextmanager
def decision_write():
	"""Server-only capability; unlike Document.flags this cannot arrive in JSON."""
	token = _trusted_write.set(True)
	try:
		yield
	finally:
		_trusted_write.reset(token)


def _get(value, key, default=None):
	return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _json(value):
	if isinstance(value, str):
		try:
			return json.loads(value)
		except (ValueError, TypeError):
			pass
	return value


def fingerprint(extraction):
	def canonical(value):
		# A JSON round trip may change 1.0 to 1 without changing the fact.
		if isinstance(value, float) and value.is_integer():
			return int(value)
		if isinstance(value, dict):
			return {key: canonical(item) for key, item in value.items()}
		if isinstance(value, list):
			return [canonical(item) for item in value]
		return value

	def rows(table, fields, status=None):
		result = []
		for row in _get(extraction, table, []) or []:
			data = {key: _json(_get(row, key)) if key.endswith("_json") else _get(row, key) for key in fields}
			if "confidence" in data:
				data["confidence"] = data["confidence"] or 0
			if status:
				data["rejected"] = _get(row, status) == "Rejected"
			result.append(data)
		return result

	data = {key: _get(extraction, key) for key in (
		"name", "target_doctype", "operation_mode", "existing_document", "selected_layout", "line_table",
	)}
	data["fields"] = rows("extracted_fields", (
		"fieldname", "fieldtype", "value", "llm_value", "llm_raw_text", "confidence", "matched_value", "source_page", "bbox_json",
	), "status")
	data["lines"] = rows("lines", (
		"table", "row_no", "raw_json", "matched_doctype", "matched_item", "source_page", "bbox_json",
	), "resolution_status")
	data["pages"] = rows("pages", (
		"page_no", "image", "source_file", "source_page_no", "layout_section", "width", "height",
	))
	data["sources"] = rows("source_files", ("source_file",))
	return hashlib.sha256(json.dumps(canonical(data), sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def payload(extraction):
	value = _json(_get(extraction, "decision_json"))
	return value if isinstance(value, dict) else {}


def bind(extraction, decision, *, input_fingerprint=None, applied=False):
	value = dict(decision)
	value["_review_binding"] = {
		"version": 1,
		"input": input_fingerprint or fingerprint(extraction),
		"applied": fingerprint(extraction) if applied else None,
	}
	extraction.decision_json = json.dumps(value, default=str, ensure_ascii=False)


def is_current(extraction, *, applied=False):
	binding = payload(extraction).get("_review_binding") or {}
	return isinstance(binding, dict) and binding.get("version") == 1 and binding.get("applied" if applied else "input") == fingerprint(extraction)


def creation_issues(extraction, *, require_applied=False):
	phase = _get(extraction, "decision_phase")
	if phase in {"Queued", "Running"}:
		return ["Reference matching is still running. Wait for it to finish."]
	if phase == "References Ready":
		return ["Local references are ready. Run the verifier before creating the document."]
	if phase == "Stale":
		return [STALE_MESSAGE]
	if require_applied and phase != "Applied to Review":
		return ["Verify and apply the Purchase Invoice company, supplier, item and tax-account choices before creating a draft."]
	if _get(extraction, "decision_json") or require_applied or phase in {"Review Ready", "Applied to Review"}:
		if not is_current(extraction, applied=phase == "Applied to Review"):
			return [STALE_MESSAGE]
		if payload(extraction).get("status") == "review" and phase != "Applied to Review":
			return ["Apply the verified choices to review before creating the document."]
	return []


def validate_save(extraction, previous):
	if _trusted_write.get():
		return
	if _get(extraction, "decision_json") != _get(previous, "decision_json") or (
		_get(extraction, "decision_phase") in {"Review Ready", "Applied to Review"}
		and _get(extraction, "decision_phase") != _get(previous, "decision_phase")
	):
		frappe.throw("Verification results can only be written by the decision service.", frappe.PermissionError)
	if previous and _get(previous, "decision_json") and fingerprint(previous) != fingerprint(extraction):
		extraction.decision_phase = "Stale"
		extraction.handoff_reason = STALE_MESSAGE
		extraction.add_processing_event("Review Staging", "Stale", STALE_MESSAGE)

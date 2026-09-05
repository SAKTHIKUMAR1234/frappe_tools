"""Durable, permission-checked handoffs owned by installed adapter packages."""

import json
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace

import frappe
from frappe.utils import now_datetime

from frappe_tools.automation import revisions
from frappe_tools.extractors.workflow import manifest

_writing = ContextVar("adapter_phase_write", default=False)


@contextmanager
def phase_write():
	token = _writing.set(True)
	try:
		yield
	finally:
		_writing.reset(token)


def definitions(plugin, ctx):
	steps = plugin.review_phases(ctx) if callable(getattr(plugin, "review_phases", None)) else []
	if not steps:
		return []
	sections = {row["key"]: row for row in manifest(plugin, ctx)["review_sections"]}
	seen, result = set(), []
	for step in steps:
		key = step.get("key")
		if not key or key in seen or not step.get("sections"):
			raise ValueError("Adapter phases need unique keys and review sections")
		seen.add(key)
		if any(key not in sections for key in step["sections"]):
			raise ValueError(f"Unknown review section in phase {key}")
		selected = [sections[key] for key in step["sections"]]
		result.append({**step, "fields": [name for row in selected for name in row.get("fields", [])],
			"tables": [row["table"] for row in selected if row.get("table")]})
	if steps and {key for step in steps for key in step["sections"]} != set(sections):
		raise ValueError("Every review section must belong to an adapter phase")
	return result


def _checkpoints(doc):
	try:
		value = json.loads(revisions._get(doc, "workflow_state_json") or "{}")
		return value if isinstance(value, dict) else {}
	except (TypeError, ValueError):
		return {}


def _fingerprint(doc, steps):
	fields = {key for step in steps for key in step["fields"]}
	tables = {key for step in steps for key in step["tables"]}
	data = {key: doc.get(key) for key in (
		"name", "target_doctype", "operation_mode", "existing_document", "selected_layout", "line_table", "pages", "source_files")}
	data["extracted_fields"] = [row for row in doc.extracted_fields if row.fieldname in fields]
	data["lines"] = [row for row in doc.lines if (row.table or doc.line_table) in tables]
	# Contract changes also invalidate checkpoints, including phases with no data.
	data["selected_layout"] = json.dumps([doc.get("selected_layout"),
		[(step["key"], step.get("version", 1), step["fields"], step["tables"]) for step in steps]])
	return revisions.fingerprint(SimpleNamespace(**data))


def state(doc, plugin, ctx):
	steps, saved, result = definitions(plugin, ctx), _checkpoints(doc), []
	active = None
	for index, step in enumerate(steps):
		checkpoint = saved.get(step["key"]) or {}
		complete = active is None and checkpoint.get("fingerprint") == _fingerprint(doc, steps[:index + 1])
		status = "complete" if complete else "active" if active is None else "waiting"
		if not complete and active is None:
			active = step["key"]
		result.append({**{key: step.get(key) for key in ("key", "label", "sections", "automation", "human_input")},
			"status": status, "can_automate": callable(step.get("automate")),
			"completed_by": checkpoint.get("user") if complete else None,
			"completed_on": checkpoint.get("time") if complete else None})
	return {"steps": result, "active": active, "ready": bool(steps) and active is None}


def creation_issues(doc, plugin, ctx):
	current = state(doc, plugin, ctx)
	if current["active"]:
		step = next(row for row in current["steps"] if row["key"] == current["active"])
		return [f"Complete the {step['label']} phase before creating the document."]
	return []


def validate_save(doc, previous):
	if _writing.get():
		return
	if doc.get("workflow_state_json") != (previous.get("workflow_state_json") if previous else None):
		frappe.throw("Phase checkpoints can only be written by the review service.", frappe.PermissionError)
	if not doc.get("workflow_state_json") or not doc.target_doctype:
		return
	from frappe_tools.extractors import get_plugin
	from frappe_tools.extractors.context import ExtractionContext
	current = state(doc, get_plugin(doc.target_doctype), ExtractionContext(doc.target_doctype))
	complete = {row["key"] for row in current["steps"] if row["status"] == "complete"}
	doc.workflow_state_json = json.dumps({key: value for key, value in _checkpoints(doc).items() if key in complete})


def advance(doc, plugin, ctx, key, operation):
	"""Caller locks the extraction and checks its revision and write permission."""
	steps = definitions(plugin, ctx)
	current = state(doc, plugin, ctx)
	if key != current["active"]:
		frappe.throw("Continue from the current phase. Reload this review if it changed.")
	step = next(row for row in steps if row["key"] == key)
	if operation == "automate":
		if not callable(step.get("automate")):
			frappe.throw("This phase has no automatic action.")
		step["automate"](ctx, doc)
	elif operation == "confirm":
		# A phase confirmation acknowledges its saved values. It cannot supply
		# new values, bypass record matching, or manufacture source evidence.
		for field in doc.extracted_fields:
			if field.fieldname in step["fields"] and field.value not in (None, "") and field.status != "Rejected":
				field.status = "Approved" if field.value == field.llm_value else "Edited"
		for line in doc.lines:
			if (line.table or doc.line_table) in step["tables"] and line.resolution_status != "Rejected":
				if line.matched_item:
					plugin.confirm_row(ctx, doc, line.row_no, line.matched_item)
		from frappe_tools.api.ocr_agent import _field_rows, _table_rows, _review_issues
		schema = plugin.schema(ctx)
		fields = _field_rows(doc, [row for row in schema["header"] if row["fieldname"] in step["fields"]])
		tables = _table_rows(doc, [row for row in schema.get("tables", []) if row["table"] in step["tables"]])
		issues = [row["message"] for row in _review_issues(doc, fields, tables, plugin, ctx, include_document=False) if row["severity"] == "error"]
		if callable(step.get("validate")):
			issues.extend(step["validate"](ctx, doc) or [])
		if issues:
			frappe.throw("<br>".join(frappe.utils.escape_html(str(message)) for message in dict.fromkeys(issues)))
		if callable(step.get("complete")):
			step["complete"](ctx, doc)
		checkpoints = _checkpoints(doc)
		checkpoints[key] = {"fingerprint": _fingerprint(doc, steps[:steps.index(step) + 1]),
			"user": frappe.session.user, "time": str(now_datetime())}
		with phase_write():
			doc.workflow_state_json = json.dumps(checkpoints)
			doc.save()
	else:
		frappe.throw("Choose automate or confirm.")
	doc.add_processing_event("Review Phase", "Completed" if operation == "confirm" else "Review",
		step.get("label") or key, {"phase": key, "operation": operation, "user": frappe.session.user})
	doc.save()
	return state(doc, plugin, ctx)

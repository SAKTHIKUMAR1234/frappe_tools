"""Correction delta capture and approval-gated memory proposals."""

import json
import re

import frappe
from frappe.utils import now_datetime


CATEGORY_FIELDS = {"_document_category", "document_category"}


def record_delta(extraction, path, model_value, human_value, agent_value=None, category=None, scope_key=None):
	deltas = _list(_get(extraction, "review_delta_json"))
	current = next((item for item in deltas if item.get("path") == path), None)
	entry = {
		"path": path,
		"model_value": current.get("model_value") if current and "model_value" in current else model_value,
		"agent_value": agent_value if agent_value is not None else (current or {}).get("agent_value"),
		"human_value": human_value,
		"category": category or (current or {}).get("category") or _category(path),
		"scope_key": scope_key or (current or {}).get("scope_key") or scope_for_path(extraction, path),
		"by": frappe.session.user,
		"on": str(now_datetime()),
	}
	if current:
		deltas[deltas.index(current)] = entry
	else:
		deltas.append(entry)
	extraction.review_delta_json = json.dumps(deltas, default=str, ensure_ascii=False)


def decision_value(extraction, path):
	"""Return the bounded agent value for a review path, if one exists."""
	decision = _object(_get(extraction, "decision_json"))
	values = decision.get("values") if isinstance(decision.get("values"), dict) else {}
	parts = str(path or "").split(":")
	if len(parts) == 2 and parts[0] == "field":
		value = values.get(parts[1])
		if value is None and isinstance(values.get("fields"), dict):
			value = values["fields"].get(parts[1])
		return value.get("value") if isinstance(value, dict) and "value" in value else value
	if len(parts) == 4 and parts[0] == "table":
		table, row_no, key = parts[1:]
		rows = values.get(table)
		if not isinstance(rows, list):
			rows = values.get("rows") if isinstance(values.get("rows"), list) else []
		for row in rows:
			if isinstance(row, dict) and str(row.get("row_no")) == row_no:
				value = row.get(key)
				return value.get("value") if isinstance(value, dict) and "value" in value else value
	return None


def scope_for_path(extraction, path):
	"""Scope a correction to its supplier/transporter layout when available."""
	parts = str(path or "").split(":")
	category = None
	if len(parts) >= 2 and parts[0] == "table" and len(parts) >= 3:
		for row in _get(extraction, "lines", []) or []:
			if str(_get(row, "row_no")) != parts[2]:
				continue
			data = _object(_get(row, "raw_json"))
			category = data.get("document_category") or data.get("_document_category")
			break
	if not category:
		for row in _get(extraction, "extracted_fields", []) or []:
			if _get(row, "fieldname") in CATEGORY_FIELDS:
				category = _get(row, "value") or _get(row, "llm_value")
				break
	normalized = re.sub(r"[^a-z0-9]+", "-", str(category or "").casefold()).strip("-")
	return f"category:{normalized[:120]}" if normalized else "global"


def memory_scope_keys(extraction):
	keys = {"global"}
	for row in _get(extraction, "extracted_fields", []) or []:
		if _get(row, "fieldname") in CATEGORY_FIELDS:
			keys.add(scope_for_path(extraction, f"field:{_get(row, 'fieldname')}"))
	for row in _get(extraction, "lines", []) or []:
		table = _get(row, "table") or _get(extraction, "line_table") or "rows"
		keys.add(scope_for_path(extraction, f"table:{table}:{_get(row, 'row_no')}:category"))
	return sorted(keys)


def approved_memory(adapter_id, scope_keys=None, min_confirmations=2):
	"""Return repeated approved corrections; never promote a conflicting answer."""
	scopes = list(dict.fromkeys(scope_keys or ["global"]))
	rows = frappe.get_all(
		"Document Memory Proposal",
		filters={"status": "Approved", "adapter_id": adapter_id, "scope_key": ["in", scopes]},
		fields=["name", "path", "scope_key", "category", "human_value"],
		order_by="reviewed_on desc",
		limit_page_length=500,
	)
	grouped = {}
	for row in rows:
		key = (row.get("scope_key") or "global", row.get("path"))
		answer = row.get("human_value") or "null"
		grouped.setdefault(key, {}).setdefault(answer, []).append(row.get("name"))
	active, conflicts = [], []
	for (scope, path), answers in grouped.items():
		if len(answers) != 1:
			conflicts.append({"scope_key": scope, "path": path, "answers": len(answers)})
			continue
		answer, sources = next(iter(answers.items()))
		if len(sources) < max(int(min_confirmations or 2), 2):
			continue
		active.append({
			"scope_key": scope,
			"path": path,
			"value": _json_value(answer),
			"confirmations": len(sources),
			"proposal_refs": sources,
		})
	return {"active": active, "conflicts": conflicts, "minimum_confirmations": max(int(min_confirmations or 2), 2)}


def propose(extraction):
	created = []
	for delta in _list(_get(extraction, "review_delta_json")):
		if delta.get("model_value") == delta.get("human_value") and delta.get("agent_value") in (None, delta.get("human_value")):
			continue
		existing = frappe.db.exists("Document Memory Proposal", {"extraction": extraction.name, "path": delta.get("path")})
		if existing:
			continue
		doc = frappe.get_doc({"doctype": "Document Memory Proposal", "status": "Pending",
			"extraction": extraction.name, "adapter_id": _get(extraction, "adapter_id") or f"ERPNext:{extraction.target_doctype}",
			"path": delta.get("path"), "category": delta.get("category") or _category(delta.get("path")),
			"scope_key": delta.get("scope_key") or "global", "model_value": json.dumps(delta.get("model_value"), default=str),
			"agent_value": json.dumps(delta.get("agent_value"), default=str), "human_value": json.dumps(delta.get("human_value"), default=str),
			"reason": "Human correction differs from OCR/agent output; review before promoting memory."})
		doc.insert(ignore_permissions=True)
		created.append(doc.name)
	return created


@frappe.whitelist()
def approve(name):
	doc = frappe.get_doc("Document Memory Proposal", name)
	doc.check_permission("write")
	if doc.status != "Pending":
		frappe.throw("Only pending memory proposals can be approved.")
	doc.db_set({"status": "Approved", "reviewed_by": frappe.session.user, "reviewed_on": now_datetime()})
	return {"proposal": doc.name, "status": "Approved", "note": "Promotion requires the adapter's repeated-confirmation threshold."}


def _list(raw):
	try:
		value = json.loads(raw or "[]")
		return value if isinstance(value, list) else []
	except (TypeError, ValueError):
		return []


def _object(raw):
	try:
		value = json.loads(raw or "{}") if isinstance(raw, str) else raw
		return value if isinstance(value, dict) else {}
	except (TypeError, ValueError):
		return {}


def _json_value(raw):
	try:
		return json.loads(raw)
	except (TypeError, ValueError):
		return raw


def _category(path):
	text = str(path or "").lower()
	if "account" in text:
		return "Accounting"
	if "item_group" in text or "hsn" in text or "uom" in text:
		return "Taxonomy"
	if "supplier" in text or "item_code" in text or "invoice" in text:
		return "Identity"
	return "OCR"


def _get(value, key, default=None):
	getter = getattr(value, "get", None)
	if not getter:
		return getattr(value, key, default)
	result = getter(key)
	return default if result is None else result

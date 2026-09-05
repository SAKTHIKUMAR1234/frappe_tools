"""Reviewer-confirmed, scoped mappings for generic document automation."""

import hashlib
import json
import re

import frappe
from frappe.utils import cint, flt, now_datetime

from frappe_tools.extractors import schema as S


def normalize(value):
	return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def scope_key(extraction, config):
	values = {row.fieldname: row.value for row in extraction.extracted_fields}
	parts = [f"{name}={normalize(values.get(name))}" for name in config.get("scope_fields") or []]
	return "|".join(parts) or "global"


def resolve(extraction, row, config, link_doctype=None):
	if not config.get("enabled"):
		return None
	source = normalize(row.llm_value or row.value)
	if not source:
		return None
	filters = {
		"target_doctype": extraction.target_doctype,
		"target_field": row.fieldname,
		"scope_key": scope_key(extraction, config),
		"source_normalized": source,
		"active": 1,
	}
	matches = frappe.get_all("Document Automation Memory", filters=filters,
		fields=["resolved_value", "confirmations", "confidence"], order_by="confirmations desc", limit=2)
	if len(matches) != 1 or cint(matches[0].confirmations) < cint(config.get("min_confirmations") or 2):
		return None
	value = matches[0].resolved_value
	if link_doctype and not frappe.db.exists(link_doctype, value):
		return None
	return {"value": value, "confirmations": cint(matches[0].confirmations), "confidence": flt(matches[0].confidence)}


def learn(extraction):
	"""Learn only explicit reviewer edits, and only after target draft creation."""
	rules = S.field_rule_map(extraction.target_doctype)
	for row in extraction.extracted_fields:
		config = (rules.get(row.fieldname) or {}).get("memory") or {}
		if not config.get("enabled") or row.status != "Edited" or not row.edited_by:
			continue
		source = normalize(row.llm_value)
		resolved = str(row.value or "").strip()
		if not source or not resolved:
			continue
		scope = scope_key(extraction, config)
		key_material = json.dumps([extraction.target_doctype, row.fieldname, scope, source, resolved], ensure_ascii=False)
		key = hashlib.sha256(key_material.encode()).hexdigest()
		doc = frappe.get_doc("Document Automation Memory", key) if frappe.db.exists("Document Automation Memory", key) else frappe.new_doc("Document Automation Memory")
		if doc.is_new():
			doc.update({"memory_key": key, "target_doctype": extraction.target_doctype, "target_field": row.fieldname,
				"scope_key": scope, "source_normalized": source, "resolved_value": resolved})
		doc.confirmations = cint(doc.confirmations) + 1
		doc.confidence = doc.confirmations / max(doc.confirmations + cint(doc.contradictions), 1)
		doc.active = doc.confirmations >= cint(config.get("min_confirmations") or 2) and doc.confidence >= 0.8
		doc.last_confirmed_on = now_datetime()
		doc.last_confirmed_by = row.edited_by
		doc.source_extraction = extraction.name
		doc.save(ignore_permissions=True)
		# Competing answers are evidence of ambiguity and are demoted.
		for rival in frappe.get_all("Document Automation Memory", filters={"target_doctype": extraction.target_doctype,
				"target_field": row.fieldname, "scope_key": scope, "source_normalized": source, "name": ["!=", key]}, pluck="name"):
			rival_doc = frappe.get_doc("Document Automation Memory", rival)
			rival_doc.contradictions = cint(rival_doc.contradictions) + 1
			rival_doc.confidence = rival_doc.confirmations / max(rival_doc.confirmations + rival_doc.contradictions, 1)
			rival_doc.active = 0
			rival_doc.save(ignore_permissions=True)

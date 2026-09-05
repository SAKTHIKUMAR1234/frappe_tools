"""Idempotent site-local seed for the first two generic automation profiles."""

import json

import frappe


def install_profiles():
	from frappe_tools.extractors import get_plugin, registered_targets
	from frappe_tools.extractors.context import ExtractionContext
	profiles = []
	for target in registered_targets():
		if frappe.db.exists("DocType", target):
			profiles.extend({**spec, "target_doctype": target} for spec in get_plugin(target).rulebook_defaults(ExtractionContext(target)))
	created = []
	for profile in profiles:
		name = frappe.db.get_value("Document Rule Book", {"title": profile["title"], "target_doctype": profile["target_doctype"]})
		if name:
			# Rules and confidence thresholds are operator configuration. Repeated
			# migration must not erase edits or silently re-enable a disabled book.
			created.append(name)
			continue
		doc = frappe.new_doc("Document Rule Book")
		doc.update({"title": profile["title"], "target_doctype": profile["target_doctype"], "enabled": 1,
			"priority": 100, "instructions": profile["instructions"], "validation_rules": json.dumps(profile["validation_rules"])})
		doc.set("field_rules", [])
		meta = frappe.get_meta(profile["target_doctype"])
		for fieldname in profile["fields"]:
			df = meta.get_field(fieldname)
			if not df:
				continue
			row = {"fieldname": fieldname, "label": df.label, "required": int(bool(df.reqd)), "minimum_confidence": 0.9,
				"auto_approve": 0}
			if df.fieldtype == "Link":
				row.update({"resolver_type": "Search", "resolver_match_fields": "name", "memory_enabled": 1,
					"memory_scope_fields": "company" if fieldname == "supplier" else "", "memory_min_confirmations": 2})
			doc.append("field_rules", row)
		doc.set("tables", [])
		for table in profile["tables"]:
			doc.append("tables", table)
		doc.save(ignore_permissions=True)
		created.append(doc.name)
	frappe.db.commit()
	return created


def ensure_default_layouts():
	"""Materialize adapter-owned layouts without replacing site configuration."""
	from frappe_tools.extractors import get_plugin, registered_targets

	created = []
	for target_doctype in registered_targets():
		if not frappe.db.exists("DocType", target_doctype):
			continue
		# A site's submitted layout is authoritative. Defaults only fill an empty
		# target, so upgrades cannot replace an operator's existing package model.
		if frappe.db.exists("Document Scanner Layout", {"layout_doctype": target_doctype}):
			continue
		for spec in get_plugin(target_doctype).scanner_layouts() or []:
			if spec.get("target_doctype") not in (None, target_doctype):
				frappe.throw(
					f"Adapter layout {spec.get('layout')} targets {spec.get('target_doctype')}, "
					f"not {target_doctype}."
				)
			sections = _layout_sections(spec)
			if not sections:
				frappe.throw(f"Adapter layout {spec.get('layout')} has no sections.")
			for section in sections:
				_ensure_section_title(section["title"])
			existing_name = frappe.db.exists("Document Scanner Layout", spec.get("layout"))
			if existing_name:
				existing_target = frappe.db.get_value(
					"Document Scanner Layout", existing_name, "layout_doctype"
				)
				frappe.throw(
					f"Scanner layout name {existing_name} already belongs to {existing_target}."
				)
			layout = frappe.get_doc({
				"doctype": "Document Scanner Layout",
				"layout_name": spec.get("layout"),
				"layout_doctype": target_doctype,
				"layout_doctype_sections": sections,
			})
			layout.insert(ignore_permissions=True)
			layout.flags.ignore_permissions = True
			layout.submit()
			created.append(layout.name)
			# Preserve the previous one-default-per-target behavior even when a
			# malformed adapter happens to return more than one initial layout.
			break
	if created:
		frappe.db.commit()
	return created


def _layout_sections(spec):
	sections = []
	for row in spec.get("sections") or []:
		title = str(row.get("title") or "").strip()
		layout_type = str(row.get("layout_type") or "").strip()
		if title and layout_type:
			sections.append({"title": title, "layout_type": layout_type})
	if not any(row["title"].casefold() == "others" for row in sections):
		sections.append({"title": "Others", "layout_type": "Series Vertical"})
	return sections


def _ensure_section_title(title):
	if frappe.db.exists("Document Layout Section Title", title):
		return
	frappe.get_doc({
		"doctype": "Document Layout Section Title",
		"title": title,
	}).insert(ignore_permissions=True)


def run_sample(path, target_doctype):
	"""Run a trusted local sample through the same durable pipeline as the UI."""
	from pathlib import Path
	from PIL import Image
	from frappe.utils.file_manager import save_file
	from frappe_tools.extractors import get_plugin, pipeline
	from frappe_tools.extractors.context import ExtractionContext

	source = Path(path).resolve()
	if not source.is_file():
		frappe.throw(f"Sample not found: {source}")
	doc = frappe.new_doc("Document Extraction")
	doc.update({"target_doctype": target_doctype, "status": "Draft"})
	tables = get_plugin(target_doctype).schema(ExtractionContext(target_doctype)).get("tables") or []
	doc.line_table = tables[0]["table"] if tables else None
	doc.insert(ignore_permissions=True)
	file_doc = save_file(source.name, source.read_bytes(), "Document Extraction", doc.name, is_private=1)
	with Image.open(source) as image:
		width, height = image.size
	doc.append("pages", {"page_no": 1, "image": file_doc.file_url, "file_name": source.name, "width": width, "height": height})
	doc.status = "Queued"
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	pipeline.run(doc.name)
	result = frappe.get_doc("Document Extraction", doc.name)
	return {
		"name": result.name, "status": result.status, "target_doctype": result.target_doctype,
		"model": result.model_used, "error": result.error_log,
		"fields": [{"fieldname": row.fieldname, "printed": row.llm_value, "value": row.value,
			"confidence": row.confidence, "status": row.status, "match_method": row.match_method} for row in result.extracted_fields],
		"tables": [{"table": row.table, "row_no": row.row_no, "values": json.loads(row.raw_json or "{}"),
			"status": row.resolution_status} for row in result.lines],
	}

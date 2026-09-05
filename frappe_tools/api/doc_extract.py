"""Whitelisted API for document extraction — thin layer over the extractors framework.

The engine (frappe_tools.extractors.pipeline) and per-(system, doctype) plugins do
the work; this module only exposes endpoints the UI calls. See
apps/frappe_tools/docs/extraction-architecture.md.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from frappe_tools.extractors import get_plugin, has_plugin, pipeline, registered_targets
from frappe_tools.extractors import service as extraction_service
from frappe_tools.extractors import schema as S
from frappe_tools.extractors.context import ExtractionContext
from frappe_tools.utils import llm


@frappe.whitelist()
def get_extractable_doctypes():
	names = registered_targets()
	return [{"doctype": n, "label": _(n)} for n in names if n]


@frappe.whitelist()
def extract_document(target_doctype=None, images=None):
	llm.ensure_ready()
	target_doctype = str(target_doctype or "").strip() or None
	if target_doctype and not frappe.has_permission(target_doctype, "create"):
		frappe.throw(_("You do not have permission to create {0}.").format(target_doctype), frappe.PermissionError)
	if target_doctype and not has_plugin(target_doctype):
		frappe.throw(_("No code-owned document adapter exists for {0}.").format(target_doctype))

	if isinstance(images, str):
		images = json.loads(images)
	if not images:
		frappe.throw(_("No scanned pages were provided."))

	extraction = extraction_service.create_extraction(target_doctype)
	extraction_service.stage_data_urls(extraction, images)
	return extraction_service.enqueue(extraction, enqueue_after_commit=True)


@frappe.whitelist()
def create_extraction_from_files(target_doctype=None, files=None, auto_process=1):
	"""Create a tracked LR/PI run from one or more already-uploaded local files."""
	if isinstance(files, str):
		files = json.loads(files)
	doc = extraction_service.create_from_file_urls(
		target_doctype,
		files,
		auto_process=bool(cint(auto_process)),
		check_permissions=True,
	)
	return {"extraction": doc.name, "status": "Preparing" if doc.auto_process else doc.status}


@frappe.whitelist()
def create_extraction_from_scanned_details(target_doctype=None, scanned_document_details=None, auto_process=1):
	"""Reprocess existing Scanned Document Detail rows through any code-owned adapter."""
	if isinstance(scanned_document_details, str):
		scanned_document_details = json.loads(scanned_document_details)
	doc = extraction_service.create_from_scanned_document_details(
		target_doctype,
		scanned_document_details,
		auto_process=bool(cint(auto_process)),
		check_permissions=True,
	)
	return {"extraction": doc.name, "status": "Preparing" if doc.auto_process else doc.status}


@frappe.whitelist()
def get_extraction(extraction):
	doc = frappe.get_doc("Document Extraction", extraction)
	doc.check_permission("read")
	plugin = get_plugin(doc.target_doctype)
	schema_by_name = {s["fieldname"]: s for s in plugin.schema(ExtractionContext(doc.target_doctype)).get("header") or []}

	pages = [{"page_no": p.page_no, "image": p.image, "width": cint(p.width), "height": cint(p.height),
		"source_file": p.source_file, "source_page_no": cint(p.source_page_no),
		"scanned_document_detail": p.scanned_document_detail}
	         for p in sorted(doc.pages, key=lambda x: cint(x.page_no))]
	sources = [{
		"file": row.source_file,
		"scanned_document_detail": row.scanned_document_detail,
		"type": row.source_type,
		"file_name": row.original_file_name,
		"content_type": row.content_type,
		"file_size": cint(row.file_size),
		"content_hash": row.content_hash,
		"page_from": cint(row.page_from),
		"page_to": cint(row.page_to),
		"status": row.status,
		"error": row.error_message,
	} for row in doc.source_files]
	events = [{
		"time": row.event_time,
		"stage": row.stage,
		"status": row.status,
		"message": row.message,
		"details": json.loads(row.details_json) if row.details_json else None,
	} for row in doc.processing_events]

	fields = []
	for f in doc.extracted_fields:
		s = schema_by_name.get(f.fieldname, {})
		fields.append({
			"name": f.name, "fieldname": f.fieldname, "label": f.label, "fieldtype": f.fieldtype,
			"value": f.value, "matched_value": f.matched_value, "match_method": f.match_method,
			"candidates": json.loads(f.candidates_json) if f.candidates_json else [],
			"llm_value": f.llm_value, "llm_raw_text": f.llm_raw_text, "confidence": flt(f.confidence),
			"bbox": json.loads(f.bbox_json) if f.bbox_json else None, "source_page": cint(f.source_page),
			"status": f.status, "options": s.get("options"), "link_doctype": s.get("link_doctype"),
			"required": s.get("required", False),
		})

	lines = [{
		"row_no": cint(l.row_no), "table": l.table or doc.line_table,
		"description": l.description, "supplier_code": l.supplier_code, "hsn": l.hsn,
		"qty": flt(l.qty), "uom": l.uom, "rate": flt(l.rate), "amount": flt(l.amount),
		"matched_item": l.matched_item, "match_method": l.match_method, "match_confidence": flt(l.match_confidence),
		"resolution_status": l.resolution_status,
		"candidates": json.loads(l.candidates_json) if l.candidates_json else [],
		"source_page": cint(l.source_page), "bbox": json.loads(l.bbox_json) if l.bbox_json else None,
	} for l in sorted(doc.lines, key=lambda x: cint(x.row_no))]

	declared, seen = [], set()
	for l in lines:
		if l["table"] and l["table"] not in seen:
			seen.add(l["table"])
			declared.append({"table": l["table"], "label": _(l["table"])})

	return {
		"name": doc.name, "target_doctype": doc.target_doctype, "status": doc.status,
		"created_document": doc.created_document, "model_used": doc.model_used, "error_log": doc.error_log,
		"line_table": doc.line_table, "tables": declared, "sources": sources, "pages": pages,
		"source_count": cint(doc.source_count), "page_count": cint(doc.page_count),
		"processing_job_id": doc.processing_job_id, "processing_started_on": doc.processing_started_on,
		"processing_completed_on": doc.processing_completed_on, "events": events, "fields": fields, "lines": lines,
		"provenance": plugin.provenance_map(doc),
	}


@frappe.whitelist()
def update_extraction_field(extraction, fieldname, value=None, status=None):
	doc = frappe.get_doc("Document Extraction", extraction)
	doc.check_permission("write")
	row = next((f for f in doc.extracted_fields if f.fieldname == fieldname), None)
	if not row:
		frappe.throw(_("Field {0} is not part of this extraction.").format(fieldname))

	changed = False
	if value is not None and value != row.value:
		row.value = value
		row.status = "Edited"
		row.edited_by = frappe.session.user
		row.edited_on = now_datetime()
		changed = True
	if status and status != row.status:
		row.status = status
		changed = True
	if changed:
		doc.save()
	return {"ok": True, "status": row.status}


@frappe.whitelist()
def update_extraction_line(extraction, row_no, description=None, uom=None, qty=None, rate=None, amount=None):
	doc = frappe.get_doc("Document Extraction", extraction)
	doc.check_permission("write")
	line = next((l for l in doc.lines if cint(l.row_no) == cint(row_no)), None)
	if not line:
		frappe.throw(_("Line {0} not found.").format(row_no))
	if description is not None:
		line.description = description
	if uom is not None:
		line.uom = uom
	if qty is not None:
		line.qty = flt(qty)
	if rate is not None:
		line.rate = flt(rate)
	if amount is not None:
		line.amount = flt(amount)
	doc.save()
	return {"ok": True}


@frappe.whitelist()
def create_document_from_extraction(extraction):
	doc = frappe.get_doc("Document Extraction", extraction)
	doc.check_permission("write")
	if doc.created_document and frappe.db.exists(doc.target_doctype, doc.created_document):
		frappe.throw(_("A document ({0}) was already created from this extraction.").format(doc.created_document))

	docname = pipeline.build(extraction)
	from frappe_tools.scan_lineage import finalize_extraction_target

	return finalize_extraction_target(doc, docname)


def _create_scanned_document(extraction_doc, docname):
	"""Compatibility alias; new code uses the reusable scan-lineage service."""
	from frappe_tools.scan_lineage import link_extraction_to_target

	return link_extraction_to_target(extraction_doc, docname)

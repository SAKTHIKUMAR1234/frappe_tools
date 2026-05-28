"""Whitelisted API for document extraction — thin layer over the extractors framework.

The engine (frappe_tools.extractors.pipeline) and per-(system, doctype) plugins do
the work; this module only exposes endpoints the UI calls. See
apps/frappe_tools/docs/extraction-architecture.md.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from frappe_tools.extractors import get_plugin, pipeline
from frappe_tools.extractors import schema as S
from frappe_tools.utils import llm


@frappe.whitelist()
def get_extractable_doctypes():
	names = frappe.get_all("Document Rule Book", filters={"enabled": 1}, distinct=True, pluck="target_doctype")
	return [{"doctype": n, "label": _(n)} for n in names if n]


@frappe.whitelist()
def extract_document(target_doctype, images):
	llm.ensure_ready()
	if not frappe.has_permission(target_doctype, "create"):
		frappe.throw(_("You do not have permission to create {0}.").format(target_doctype), frappe.PermissionError)
	if not S.get_rule_books(target_doctype):
		frappe.throw(_("No enabled rule book exists for {0}. Create one first.").format(target_doctype))

	if isinstance(images, str):
		images = json.loads(images)
	if not images:
		frappe.throw(_("No scanned pages were provided."))

	tables = S.rulebook_tables(target_doctype)
	extraction = frappe.new_doc("Document Extraction")
	extraction.target_doctype = target_doctype
	extraction.status = "Extracting"
	extraction.line_table = tables[0]["table"] if tables else None
	extraction.insert()

	for idx, data_url in enumerate(images, 1):
		url, w, h = pipeline.save_page_image(data_url, extraction.name, idx)
		extraction.append("pages", {"page_no": idx, "image": url, "file_name": url.rsplit("/", 1)[-1], "width": w, "height": h})
	extraction.save()

	frappe.enqueue("frappe_tools.extractors.pipeline.run", queue="long", timeout=900,
	               extraction_name=extraction.name, enqueue_after_commit=True)
	return {"extraction": extraction.name, "status": extraction.status}


@frappe.whitelist()
def get_extraction(extraction):
	doc = frappe.get_doc("Document Extraction", extraction)
	doc.check_permission("read")
	schema_by_name = {s["fieldname"]: s for s in S.build_header_schema(doc.target_doctype)}
	plugin = get_plugin(doc.target_doctype)

	pages = [{"page_no": p.page_no, "image": p.image, "width": cint(p.width), "height": cint(p.height)}
	         for p in sorted(doc.pages, key=lambda x: cint(x.page_no))]

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
		"line_table": doc.line_table, "tables": declared, "pages": pages, "fields": fields, "lines": lines,
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

	scanned = _create_scanned_document(doc, docname)
	doc.created_document = docname
	doc.scanned_document = scanned
	doc.status = "Created"
	doc.save()
	return {"doctype": doc.target_doctype, "docname": docname}


def _create_scanned_document(extraction_doc, docname):
	try:
		scanned = frappe.new_doc("Scanned Document")
		scanned._doctype = extraction_doc.target_doctype
		scanned._docname = docname
		scanned.flags.ignore_mandatory = True
		scanned.insert(ignore_permissions=True)
		return scanned.name
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Linking Scanned Document to created doc failed")
		return None

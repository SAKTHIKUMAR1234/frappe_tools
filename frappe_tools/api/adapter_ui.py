"""Permission-aware calls from installed adapter review components."""

import json

import frappe
from frappe import _
from frappe.model import get_permitted_fields

from frappe_tools.api.ocr_agent import _get_extraction
from frappe_tools.extractors import get_plugin
from frappe_tools.extractors.context import ExtractionContext


@frappe.whitelist(methods=["POST"])
def run_action(extraction, action, values=None, modified=None):
	doc = _get_extraction(extraction, "write", for_update=True)
	if doc.status != "Review" or doc.get("decision_phase") in {"Queued", "Running"}:
		frappe.throw(_("Wait until this document is ready for review."))
	if not modified or str(doc.modified) != str(modified):
		frappe.throw(_("This review changed. Reload it before continuing."), frappe.TimestampMismatchError)
	ctx = ExtractionContext(doc.target_doctype)
	plugin = get_plugin(doc.target_doctype)
	spec = plugin.review_actions(ctx).get(action)
	if not spec or not callable(spec.get("handler")):
		frappe.throw(_("This action is not available for this document."), frappe.PermissionError)
	for doctype, ptype in spec.get("permissions", []):
		frappe.has_permission(doctype, ptype=ptype, throw=True)
	values = json.loads(values) if isinstance(values, str) else values
	if values is not None and not isinstance(values, dict):
		frappe.throw(_("Action input must be an object."))
	# One HTTP transaction keeps a created master and its review mapping atomic.
	result = spec["handler"](ctx, doc, values or {})
	doc.add_processing_event("Human Review", "Completed", spec.get("label") or action,
		{"action": action, "user": frappe.session.user})
	doc.save()
	return result


@frappe.whitelist()
def search_link(extraction, query, text="", value=None):
	"""Search or preview only an adapter-declared Link and permitted fields."""
	doc = _get_extraction(extraction, "read")
	plugin = get_plugin(doc.target_doctype)
	spec = plugin.link_queries(ExtractionContext(doc.target_doctype), doc).get(query)
	if not spec:
		frappe.throw(_("This Link control is not available."), frappe.PermissionError)
	doctype = spec["doctype"]
	frappe.has_permission(doctype, "read", throw=True)
	meta = frappe.get_meta(doctype)
	permitted = set(get_permitted_fields(doctype, permission_type="read"))
	fields = ["name"] + [name for name in spec.get("fields", [])
		if name != "name" and name in permitted and meta.has_field(name)
		and meta.get_field(name).fieldtype not in {"Password", "Table", "Table MultiSelect"}]
	filters = [[key, *(condition if isinstance(condition, (list, tuple)) else ["=", condition])]
		for key, condition in (spec.get("filters") or {}).items()]
	if value:
		filters.append(["name", "=", value])
	search_fields = [name for name in spec.get("search_fields", ["name"]) if name in fields]
	rows = frappe.get_list(doctype, fields=fields, filters=filters,
		or_filters=[[name, "like", f"%{str(text)[:140]}%"] for name in search_fields] if text and not value else None,
		limit_page_length=1 if value else 10, order_by="modified desc")
	title = meta.get_title_field() or "name"
	return [{"value": row.name, "label": row.get(title) or row.name,
		"details": [{"label": meta.get_field(name).label or name, "value": row.get(name)}
			for name in fields if name != "name" and row.get(name) not in (None, "")]}
		for row in rows if frappe.has_permission(doctype, doc=row.name, ptype="read")]


@frappe.whitelist(methods=["POST"])
def advance_phase(extraction, phase, operation, modified):
	doc = _get_extraction(extraction, "write", for_update=True)
	if doc.status != "Review" or doc.get("decision_phase") in {"Queued", "Running"}:
		frappe.throw(_("Wait until this document is ready for review."))
	if not modified or str(doc.modified) != str(modified):
		frappe.throw(_("This review changed. Reload it before continuing."), frappe.TimestampMismatchError)
	from frappe_tools.extractors.phases import advance
	return advance(doc, get_plugin(doc.target_doctype), ExtractionContext(doc.target_doctype), phase, operation)

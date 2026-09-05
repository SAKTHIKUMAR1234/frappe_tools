"""Document automation API used by the standalone PrimeVue workspace.

The UI sees one predictable workflow while this facade delegates extraction,
bounded subscription-agent reasoning, and controlled document construction to
code-owned adapters.
"""

import json
import os

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime

from frappe_tools.api import doc_extract
from frappe_tools.extractors import get_plugin, grounding, has_plugin, layout_routing, pipeline, service
from frappe_tools.extractors import schema as S
from frappe_tools.automation import learning as automation_learning
from frappe_tools.automation import runtime as automation_runtime
from frappe_tools.automation import revisions
from frappe_tools.extractors.context import ExtractionContext
from frappe_tools.extractors.workflow import manifest as workflow_manifest
from frappe_tools.extractors import phases
from frappe_tools.utils import llm

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_DOCUMENT_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | {".pdf"}
EDITABLE_STATUSES = {"Draft", "Review", "Failed"}
QUEUE_STATUS_MAP = {
	"draft": {"Draft"},
	"pending": {"Preparing", "Classifying", "Queued", "Extracting"},
	"ready": {"Classification Handoff", "Layout Handoff", "Review"},
	"validated": {"Created", "Attached"},
	"error": {"Failed"},
}
DECISION_PENDING_PHASES = {"Queued", "Running"}


def has_app_permission():
	if frappe.session.user == "Guest":
		return False
	roles = set(frappe.get_roles())
	return bool(roles.intersection({"Scanner User", "System Manager"}))


@frappe.whitelist()
def get_boot():
	"""Return only the configuration required to render the workspace."""
	doctypes = []
	layouts = []
	for item in doc_extract.get_extractable_doctypes():
		if frappe.has_permission(item["doctype"], "read"):
			profile = _profile_summary(item["doctype"])
			profile["can_create"] = bool(frappe.has_permission(item["doctype"], "create"))
			doctypes.append(profile)
			for layout in layout_routing.candidate_layouts(item["doctype"]):
				layouts.append({
					**layout,
					"target_label": profile["label"],
					"section_count": len(layout["sections"]),
					"can_create": profile["can_create"],
					"workflow": profile["workflow"],
				})

	queue = get_queue(page_length=25)
	user_id = frappe.session.user
	user_name = frappe.db.get_value("User", user_id, "full_name") or user_id
	return {
		"user": user_name,
		"user_id": user_id,
		"site_name": frappe.local.site,
		"doctypes": doctypes,
		"layouts": layouts,
		"queue": queue,
		"limits": {"max_pages": 20, "max_file_mb": 15, "accepted": sorted(ALLOWED_DOCUMENT_EXTENSIONS)},
		"processing": {**pipeline.processing_config(), "provider": llm.readiness(), "agents": _processing_agents()},
	}


def _processing_agents():
	"""Expose model identities for the UI, never credentials or provider calls."""
	settings = frappe.get_single("Document Extraction Settings")
	result = {}
	for role, field, default in (
		("extractor", "vision_ai_model", "Document Automation Luna"),
		("verifier", "decision_ai_model", "Document Verification Sol"),
	):
		name = settings.get(field) or default
		model = frappe.db.get_value("AI Model", name, ["model_label", "model_id", "enabled"], as_dict=True)
		result[role] = {"name": name, "label": model.model_label if model else name,
			"model": model.model_id if model else None, "enabled": bool(model and model.enabled)}
	return result


@frappe.whitelist()
def get_profile_health(target_doctype):
	"""Preflight a configuration before a user spends a model call."""
	errors, warnings = [], []
	if not frappe.db.exists("DocType", target_doctype):
		return {"ready": False, "errors": [_("Unknown DocType {0}.").format(target_doctype)], "warnings": []}
	if not has_plugin(target_doctype):
		errors.append(_("No code-owned document adapter exists."))
	profile = get_plugin(target_doctype).schema(ExtractionContext(target_doctype))
	if not profile.get("header"):
		errors.append(_("No extractable header fields are configured."))
	if not frappe.has_permission(target_doctype, "create"):
		errors.append(_("Current user cannot create this DocType."))
	provider = llm.readiness()
	if not provider.get("ready"):
		errors.append(_("Vision provider is not ready."))
	for field in profile.get("header") or []:
		if field.get("memory", {}).get("enabled") and not field.get("memory", {}).get("scope_fields"):
			warnings.append(_("{0} uses global memory scope.").format(field.get("label")))
	return {"ready": not errors, "errors": errors, "warnings": warnings,
		"profile": {"doctype": target_doctype, "fields": len(profile.get("header") or []),
			"tables": [item["table"] for item in profile.get("tables") or []]}, "provider": provider}


@frappe.whitelist()
def validate_attachment_target(target_doctype, document_name):
	"""Validate a direct-attachment destination before the phone starts uploading."""
	target_doctype = str(target_doctype or "").strip()
	document_name = str(document_name or "").strip()
	if not target_doctype or not frappe.db.exists("DocType", target_doctype):
		frappe.throw(_("Unknown target DocType."))
	if not document_name or not frappe.db.exists(target_doctype, document_name):
		frappe.throw(_("{0} {1} does not exist.").format(target_doctype, document_name))
	if not frappe.has_permission(target_doctype, doc=document_name, ptype="write"):
		frappe.throw(_("You do not have permission to attach scans to this document."), frappe.PermissionError)
	return {"doctype": target_doctype, "name": document_name}


@frappe.whitelist()
def get_queue(status="all", start=0, page_length=25, search=None, target_doctype=None,
		created="all", date_from=None, date_to=None, sort_by="modified", sort_order="desc"):
	"""Return the current user's permission-filtered extraction work queue."""
	status = str(status or "all").lower()
	if status != "all" and status not in QUEUE_STATUS_MAP:
		frappe.throw(_("Unknown queue status {0}.").format(status))
	allowed_targets = [
		item["doctype"] for item in doc_extract.get_extractable_doctypes()
		if frappe.has_permission(item["doctype"], "read")
	]
	if not allowed_targets:
		return _empty_queue_result(status, start, page_length)
	target_doctype = str(target_doctype or "").strip()
	if target_doctype and target_doctype not in allowed_targets:
		frappe.throw(_("This document type is not available."), frappe.PermissionError)
	created = str(created or "all").lower()
	if created not in {"all", "yes", "no"}:
		frappe.throw(_("Unknown created-document filter."))
	sort_by = str(sort_by or "modified").lower()
	if sort_by not in {"modified", "creation", "target_doctype", "status"}:
		frappe.throw(_("Unknown queue sort field."))
	sort_order = str(sort_order or "desc").lower()
	if sort_order not in {"asc", "desc"}:
		frappe.throw(_("Unknown queue sort order."))
	start = max(cint(start), 0)
	page_length = min(max(cint(page_length), 1), 100)

	filters = [["Document Extraction", "owner", "=", frappe.session.user]]
	if target_doctype:
		filters.append(["Document Extraction", "target_doctype", "=", target_doctype])
	if date_from:
		filters.append(["Document Extraction", "creation", ">=", str(getdate(date_from))])
	if date_to:
		filters.append(["Document Extraction", "creation", "<=", f"{getdate(date_to)} 23:59:59.999999"])
	if created == "yes":
		filters.append(["Document Extraction", "created_document", "is", "set"])
	elif created == "no":
		filters.append(["Document Extraction", "created_document", "is", "not set"])
	rows = frappe.get_list(
		"Document Extraction",
		fields=[
			"name", "target_doctype", "status", "modified", "creation", "created_document",
			"cost_usd", "model_used", "owner", "error_log", "classification_phase",
			"classification_confidence", "layout_phase", "selected_layout", "decision_phase",
			"processing_job_id", "classification_job_id", "decision_job_id", "processing_started_on",
		],
		filters=filters,
		or_filters=[
			["Document Extraction", "target_doctype", "in", allowed_targets],
			["Document Extraction", "target_doctype", "is", "not set"],
		],
		order_by=f"{sort_by} {sort_order}, name {sort_order}",
		limit_page_length=0,
	)
	term = str(search or "").strip().lower()
	if term:
		row_names = [row.name for row in rows]
		file_text = {}
		for page in (frappe.get_all(
			"Document Extraction Page",
			filters={"parent": ["in", row_names], "parenttype": "Document Extraction"},
			fields=["parent", "file_name"],
			limit_page_length=0,
		) if row_names else []):
			file_text[page.parent] = f"{file_text.get(page.parent, '')} {page.file_name or ''}".lower()
		rows = [row for row in rows if term in (
			" ".join(str(row.get(key) or "").lower() for key in (
				"name", "target_doctype", "created_document", "status",
			)) + file_text.get(row.name, "")
		)]

	counts = _empty_queue_counts()
	for row in rows:
		group = _queue_status(row.status, row.decision_phase)
		counts[group] += 1
		counts["all"] += 1
	if status != "all":
		rows = [row for row in rows if _queue_status(row.status, row.decision_phase) == status]
	total = len(rows)
	rows = rows[start:start + page_length]
	names = [row.name for row in rows]
	page_rows = frappe.get_all(
		"Document Extraction Page",
		filters={"parent": ["in", names], "parenttype": "Document Extraction"},
		fields=["parent", "file_name", "idx"],
		order_by="parent asc, idx asc",
	) if names else []
	page_details = {}
	for page in page_rows:
		detail = page_details.setdefault(page.parent, {"page_count": 0, "file_name": None})
		detail["page_count"] += 1
		if not detail["file_name"]:
			detail["file_name"] = page.file_name

	can_write = frappe.has_permission("Document Extraction", "write")
	items = []
	for row in rows:
		pages = page_details.get(row.name, {"page_count": 0, "file_name": None})
		can_create_target = bool(
			frappe.has_permission(row.target_doctype, "create") if row.target_doctype else can_write
		)
		can_read_target = bool(row.target_doctype and frappe.has_permission(row.target_doctype, "read"))
		decision_pending = row.status == "Review" and row.decision_phase in DECISION_PENDING_PHASES
		job = _job_state(row)
		public_row = {key: row.get(key) for key in (
			"name", "target_doctype", "status", "modified", "creation", "created_document",
			"classification_phase", "classification_confidence", "layout_phase",
			"selected_layout", "decision_phase",
		)}
		items.append({
			**public_row,
			**pages,
			"queue_status": _queue_status(row.status, row.decision_phase),
			"decision_pending": decision_pending,
			"job_state": job["state"],
			"job_label": job["label"],
			"timeout_seconds": pipeline.processing_timeout_seconds(pages["page_count"]),
			"can_review": bool(can_write and can_create_target and row.status == "Review" and not decision_pending),
			"can_retry": bool(can_write and can_create_target and (row.status == "Failed" or job["state"] == "stale")),
			"can_open_created": bool(can_read_target and row.created_document),
		})
	return {
		"items": items,
		"counts": counts,
		"status": status,
		"start": start,
		"page_length": page_length,
		"total": total,
		"has_previous": start > 0,
		"has_more": start + len(items) < total,
		"filters": {
			"search": search or "", "target_doctype": target_doctype, "created": created,
			"date_from": date_from, "date_to": date_to, "sort_by": sort_by, "sort_order": sort_order,
		},
	}


@frappe.whitelist()
def retry_session(extraction):
	"""Requeue a failed extraction without creating a second parent record."""
	doc = _get_extraction(extraction, "write")
	if doc.target_doctype:
		frappe.has_permission(doc.target_doctype, "create", throw=True)
	else:
		from frappe_tools.extractors.classification import candidate_profiles

		if not candidate_profiles(check_permissions=True):
			frappe.throw(_("No document adapter is available for automatic classification."))
	job = _job_state(doc)
	if job["state"] in {"queued", "started"}:
		frappe.throw(_("This document is still processing."))
	if doc.status != "Failed" and job["state"] != "stale":
		frappe.throw(_("Only failed or stalled documents can be retried."))
	page_count = len([page for page in doc.pages if page.image])
	if not page_count:
		frappe.throw(_("This extraction has no readable pages."))
	doc.status = "Draft"
	doc.error_log = None
	doc.flags.skip_auto_process = True
	doc.save()
	job = service.enqueue(doc, enqueue_after_commit=True)
	return {"extraction": doc.name, "status": doc.status, **job}


@frappe.whitelist()
def create_session(target_doctype=None, selected_layout=None, operation_mode="Create New", existing_document=None):
	"""Create a durable local session before provider-dependent processing.

	Capture/staging must remain available when the optional vision provider is
	unconfigured or temporarily unavailable. The worker performs its own provider
	readiness check when extraction actually starts.
	"""
	target_doctype = str(target_doctype or "").strip() or None
	selected_layout = str(selected_layout or "").strip() or None
	doc = service.create_extraction(
		target_doctype,
		selected_layout=selected_layout,
		operation_mode=operation_mode,
		existing_document=existing_document,
	)
	return {
		"extraction": doc.name,
		"status": doc.status,
		"target_doctype": doc.target_doctype,
		"selected_layout": doc.selected_layout,
		"operation_mode": doc.operation_mode,
		"existing_document": doc.existing_document,
	}


@frappe.whitelist()
def start_session(extraction, file_urls):
	"""Verify uploaded private Files, attach ordered pages and enqueue one run."""
	doc = _get_extraction(extraction, "write")
	if doc.status not in EDITABLE_STATUSES:
		frappe.throw(_("This extraction has already started."))
	urls = _as_list(file_urls)
	if not urls:
		frappe.throw(_("Upload at least one document page."))
	if len(urls) > 20:
		frappe.throw(_("A maximum of 20 pages is supported per extraction."))
	target_doctype = getattr(doc, "target_doctype", None)
	if target_doctype and layout_routing.candidate_layouts(target_doctype) and not getattr(doc, "selected_layout", None):
		frappe.throw(_("Select a Document Scanner Layout before uploading this batch."))

	pages = []
	for file_url in urls:
		pages.extend(_verified_document_pages(doc.name, file_url, len(pages) + 1, 20 - len(pages)))
	if len(pages) > 20:
		frappe.throw(_("A maximum of 20 rendered pages is supported per extraction."))

	doc.set("pages", [])
	for page in pages:
		doc.append("pages", page)
	doc.status = "Queued"
	doc.error_log = None
	doc.save()
	job = service.enqueue(doc, enqueue_after_commit=True)
	return {"extraction": doc.name, "status": doc.status, **job}


@frappe.whitelist()
def get_uploaded_files(extraction):
	"""Return local files already attached to a durable capture session.

	Mobile upload retries reconcile by content hash so a response timeout cannot
	create a second page or a second extraction run.
	"""
	doc = _get_extraction(extraction, "read")
	return frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "Document Extraction",
			"attached_to_name": doc.name,
			"is_folder": 0,
		},
		fields=["file_name", "file_url", "content_hash", "file_size", "is_private"],
		order_by="creation asc",
	)


@frappe.whitelist()
def confirm_classification(extraction, target_doctype):
	"""Human handoff action for an uncertain automatic document type."""
	from frappe_tools.extractors.classification import accept_target

	return accept_target(extraction, target_doctype)


@frappe.whitelist()
def confirm_layout(extraction, layout):
	"""Human handoff action for an uncertain document-package layout."""
	from frappe_tools.extractors.layout_routing import accept_layout

	return accept_layout(extraction, layout)


@frappe.whitelist()
def get_run(extraction):
	"""Return a provider-neutral view model for any configured target DocType."""
	doc = _get_extraction(extraction, "read")
	plugin = get_plugin(doc.target_doctype) if doc.target_doctype else None
	ctx = ExtractionContext(doc.target_doctype) if doc.target_doctype else None
	profile = plugin.schema(ctx) if plugin else {"header": [], "tables": []}
	workflow = workflow_manifest(plugin, ctx, profile) if plugin else None
	fields = _field_rows(doc, profile.get("header") or [])
	tables = _table_rows(doc, profile.get("tables") or [])
	issues = _review_issues(doc, fields, tables, plugin, ctx) if plugin else []
	action_preview = _action_preview(doc, fields, tables, issues, workflow) if plugin else {
		"kind": "classification",
		"label": _("Confirm document type"),
		"target_doctype": None,
		"document_status": None,
		"fields": [],
		"tables": [],
		"field_count": 0,
		"row_count": 0,
		"blockers": [_("Confirm the document type before extraction.")],
		"ready": False,
		"idempotent": True,
		"will_submit": False,
	}
	return {
		"name": doc.name,
		"target_doctype": doc.target_doctype,
		"modified": str(doc.modified),
		"target_label": workflow["label"] if workflow else _("Detecting document"),
		"workflow": workflow,
		"adapter_ui": _adapter_ui(plugin, ctx) if plugin else None,
		"review_phases": phases.state(doc, plugin, ctx) if plugin else None,
		"operation_mode": doc.get("operation_mode") or "Create New",
		"status": doc.status,
		"created_document": doc.created_document,
		"model_used": doc.model_used,
		"cost_usd": flt(doc.cost_usd, 6),
		"error": _("This document could not be processed. Retry it, or contact your administrator if it fails again.")
			if doc.status == "Failed" else None,
		"processing": {
			**pipeline.processing_config(),
			"page_count": len(doc.pages),
			"timeout_seconds": pipeline.processing_timeout_seconds(len(doc.pages)),
		},
		"pages": [
			{
				"page_no": cint(page.page_no),
				"original_page_no": cint(page.get("original_page_no")),
				"layout_section": page.get("layout_section"),
				"layout_type": page.get("layout_type"),
				"page_type": page.get("page_type"),
				"layout_confidence": flt(page.get("layout_confidence")) / 100,
				"image": f"/api/method/frappe_tools.api.ocr_agent.get_page_image?extraction={doc.name}&page_no={cint(page.page_no)}",
				"source_file": page.image,
				"width": cint(page.width),
				"height": cint(page.height),
			}
			for page in sorted(doc.pages, key=lambda row: cint(row.page_no))
		],
		"fields": fields,
		"tables": tables,
		"issues": issues,
		"action_preview": action_preview,
		"decision": {
			"phase": doc.get("decision_phase"),
			"handoff_reason": doc.get("handoff_reason"),
			"result": _public_reason_result(doc.get("decision_json")),
		},
		"classification": {
			"phase": doc.get("classification_phase"),
			"confidence": flt(doc.get("classification_confidence")) / 100,
			"result": _public_reason_result(doc.get("classification_json")),
			"requires_human": doc.status == "Classification Handoff",
		},
		"layout": {
			"phase": doc.get("layout_phase"),
			"selected": doc.get("selected_layout"),
			"confidence": flt(doc.get("layout_confidence")) / 100,
			"result": _public_reason_result(doc.get("layout_json")),
			"candidates": [profile["layout"] for profile in layout_routing.candidate_layouts(
				doc.target_doctype
			)] if doc.target_doctype else [],
			"requires_human": doc.status == "Layout Handoff",
		},
		"summary": {
			"errors": sum(1 for issue in issues if issue["severity"] == "error"),
			"warnings": sum(1 for issue in issues if issue["severity"] == "warning"),
			"ready": action_preview["ready"],
		},
		"stages": _stages(doc.status, doc.get("decision_phase")),
	}


@frappe.whitelist()
def get_page_image(extraction, page_no):
	"""Permission-checked image stream independent of dev-server private routing."""
	doc = _get_extraction(extraction, "read")
	page = next((row for row in doc.pages if cint(row.page_no) == cint(page_no)), None)
	if not page or not page.image:
		frappe.throw(_("Document page was not found."))
	file_doc = frappe.get_doc("File", {"file_url": page.image})
	frappe.local.response.filename = file_doc.file_name
	frappe.local.response.filecontent = file_doc.get_content()
	frappe.local.response.type = "download"
	frappe.local.response.display_content_as = "inline"


@frappe.whitelist()
def prepare_decision(extraction):
	doc = _get_extraction(extraction, "write")
	if doc.status != "Review":
		frappe.throw(_("Decision preparation requires a completed extraction."))
	return automation_runtime.prepare(doc.name)


@frappe.whitelist()
def reprocess_decision(extraction):
	"""Rerun references, agent matching and validators from stored OCR data.

	This path intentionally never invokes layout routing or the vision extractor.
	Use it after a user creates/fixes an Item, HSN, Supplier, account or another
	local master requested by an actionable handoff.
	"""
	doc = _get_extraction(extraction, "write")
	if doc.status != "Review":
		frappe.throw(_("Decision reprocessing requires a completed extraction."))
	return automation_runtime.reprocess(doc.name)


@frappe.whitelist()
def run_decision(extraction):
	doc = _get_extraction(extraction, "write")
	if doc.status != "Review":
		frappe.throw(_("The decision agent runs only during review."))
	job = _job_state(doc)
	if job["state"] in {"queued", "started"}:
		frappe.throw(_("This document decision is already running."))
	return automation_runtime.enqueue_decision(doc.name, enqueue_after_commit=True, force=True)


@frappe.whitelist()
def apply_decision(extraction):
	doc = _get_extraction(extraction, "write")
	if doc.status != "Review":
		frappe.throw(_("A decision can be applied only during review."))
	return automation_runtime.apply(doc.name)


@frappe.whitelist()
def update_field(extraction, fieldname, value=None, rejected=0):
	doc = _get_extraction(extraction, "write")
	if doc.status != "Review":
		frappe.throw(_("Fields can be edited only during review."))
	row = next((item for item in doc.extracted_fields if item.fieldname == fieldname), None)
	if not row:
		frappe.throw(_("Field {0} is not part of this extraction.").format(fieldname))
	plugin = get_plugin(doc.target_doctype)
	profile = plugin.schema(ExtractionContext(doc.target_doctype))
	schema = {item["fieldname"]: item for item in profile.get("header") or []}
	field = schema.get(fieldname)
	if not field:
		frappe.throw(_("Field {0} is not extractable.").format(fieldname))

	new_value = "" if value is None else str(value)
	if new_value != str(row.value or ""):
		path = f"field:{fieldname}"
		automation_learning.record_delta(
			doc,
			path,
			getattr(row, "llm_value", None),
			new_value,
			agent_value=automation_learning.decision_value(doc, path),
		)
	row.value = new_value
	row.status = "Rejected" if cint(rejected) else "Edited"
	row.edited_by = frappe.session.user
	row.edited_on = now_datetime()
	if field.get("link_doctype") and row.value:
		if frappe.db.exists(field["link_doctype"], row.value):
			row.matched_value = row.value
			row.match_method = "user-confirmed"
		else:
			row.matched_value = None
			row.match_method = None
	doc.save()
	return get_run(doc.name)


@frappe.whitelist()
def update_table_row(extraction, table, row_no, values):
	doc = _get_extraction(extraction, "write")
	if doc.status != "Review":
		frappe.throw(_("Rows can be edited only during review."))
	values = _as_dict(values)
	plugin = get_plugin(doc.target_doctype)
	profile = plugin.schema(ExtractionContext(doc.target_doctype))
	table_schema = next((item for item in profile.get("tables") or [] if item["table"] == table), None)
	if not table_schema:
		frappe.throw(_("Table {0} is not configured for extraction.").format(table))
	allowed = {column["key"] for column in table_schema.get("columns") or []}
	line = next(
		(item for item in doc.lines if (item.table or doc.line_table) == table and cint(item.row_no) == cint(row_no)),
		None,
	)
	if not line:
		frappe.throw(_("Row {0} was not found.").format(row_no))
	data = _json_object(line.raw_json)
	before = dict(data)
	for key, value in values.items():
		if key in allowed:
			data[key] = value
			if before.get(key) != value:
				path = f"table:{table}:{row_no}:{key}"
				automation_learning.record_delta(
					doc,
					path,
					before.get(key),
					value,
					agent_value=automation_learning.decision_value(doc, path),
				)
	line.raw_json = json.dumps(data, ensure_ascii=False)
	_mirror_common_line_values(line, data)
	# Saving a row is its explicit reviewer-acceptance event. Link, required
	# value, evidence and arithmetic validation can still block creation.
	line.resolution_status = "Confirmed"
	doc.save()
	return get_run(doc.name)


@frappe.whitelist()
def refresh_grounding(extraction):
	"""Replace unverified model coordinates with local OCR word boxes."""
	doc = _get_extraction(extraction, "write")
	if doc.status not in {"Review", "Created"}:
		frappe.throw(_("Highlights can be refined only after extraction."))
	grounding.apply(doc)
	doc.save()
	return get_run(doc.name)


@frappe.whitelist()
def update_bbox(extraction, scope, page, bbox, fieldname=None, table=None, row_no=None):
	"""Persist a reviewer-corrected source region using normalized coordinates."""
	doc = _get_extraction(extraction, "write")
	if doc.status != "Review":
		frappe.throw(_("Highlights can be corrected only during review."))
	page = cint(page)
	page_row = next((item for item in doc.pages if cint(item.page_no) == page), None)
	if not page_row:
		frappe.throw(_("Unknown source page."))
	box = pipeline.validate_bbox_for_page(_as_dict(bbox), page_row.width, page_row.height)
	if not box:
		frappe.throw(_("Draw a valid source region."))

	row = None
	if scope == "field":
		row = next((item for item in doc.extracted_fields if item.fieldname == fieldname), None)
	elif scope == "table":
		row = next(
			(item for item in doc.lines if (item.table or doc.line_table) == table and cint(item.row_no) == cint(row_no)),
			None,
		)
	if not row:
		frappe.throw(_("The evidence target was not found."))

	previous = _json_object(row.bbox_json)
	model = previous.get("model_bbox") if isinstance(previous.get("model_bbox"), dict) else {
		key: previous[key] for key in ("x", "y", "w", "h") if key in previous
	}
	box.update({"source": "manual", "verified": True, "score": 1})
	if len(model) == 4:
		box["model_bbox"] = model
		box["model_page"] = cint(previous.get("model_page")) or cint(row.source_page)
	row.bbox_json = json.dumps(box, ensure_ascii=False)
	row.source_page = page
	doc.save()
	return get_run(doc.name)


@frappe.whitelist()
def search_link(doctype, text="", limit=10):
	"""Permission-aware exact/fuzzy lookup used by dynamic Link inputs."""
	frappe.has_permission(doctype, "read", throw=True)
	meta = frappe.get_meta(doctype)
	title_field = meta.get_title_field() or "name"
	fields = ["name"]
	if title_field != "name" and meta.has_field(title_field):
		fields.append(title_field)
	filters = {}
	or_filters = [[doctype, "name", "like", f"%{text}%"]]
	if title_field != "name" and meta.has_field(title_field):
		or_filters.append([doctype, title_field, "like", f"%{text}%"])
	rows = frappe.get_list(
		doctype,
		fields=fields,
		filters=filters,
		or_filters=or_filters,
		limit_page_length=min(max(cint(limit), 1), 20),
		order_by="modified desc",
	)
	return [
		{"value": row.name, "label": row.get(title_field) or row.name}
		for row in rows
	]


@frappe.whitelist()
def create_draft(extraction):
	"""Validate and create exactly one draft target document."""
	doc = _get_extraction(extraction, "write", for_update=True)
	if doc.operation_mode == "Attach Existing":
		frappe.has_permission(doc.target_doctype, doc=doc.existing_document, ptype="write", throw=True)
	else:
		frappe.has_permission(doc.target_doctype, "create", throw=True)
	if doc.created_document and frappe.db.exists(doc.target_doctype, doc.created_document):
		return {"doctype": doc.target_doctype, "docname": doc.created_document, "already_created": True}
	if doc.status != "Review":
		frappe.throw(_("This extraction is not ready for document creation."))

	plugin = get_plugin(doc.target_doctype)
	ctx = ExtractionContext(doc.target_doctype)
	profile = plugin.schema(ctx)
	fields = _field_rows(doc, profile.get("header") or [])
	tables = _table_rows(doc, profile.get("tables") or [])
	issues = _review_issues(doc, fields, tables, plugin, ctx)
	errors = _action_preview(doc, fields, tables, issues)["blockers"]
	if errors:
		frappe.throw("<br>".join(errors))

	docname = pipeline.build(doc.name)
	automation_learning.propose(doc)
	from frappe_tools.scan_lineage import finalize_extraction_target

	result = finalize_extraction_target(
		doc,
		docname,
		status="Attached" if doc.operation_mode == "Attach Existing" else "Created",
		stage="Validated Staging Update" if doc.operation_mode == "Attach Existing" else "Target Document",
	)
	return {**result, "already_created": False}


def _validate_target(target_doctype):
	if not frappe.db.exists("DocType", target_doctype):
		frappe.throw(_("Unknown DocType {0}.").format(target_doctype))
	frappe.has_permission(target_doctype, "read", throw=True)
	frappe.has_permission(target_doctype, "create", throw=True)
	if not has_plugin(target_doctype):
		frappe.throw(_("No code-owned document adapter exists for {0}.").format(target_doctype))


def _profile_summary(target_doctype):
	plugin = get_plugin(target_doctype)
	ctx = ExtractionContext(target_doctype)
	profile = plugin.schema(ctx)
	workflow = workflow_manifest(plugin, ctx, profile)
	return {
		"doctype": target_doctype,
		"label": workflow["label"],
		"workflow": workflow,
		"field_count": len(profile.get("header") or []),
		"tables": [
			{"fieldname": item["table"], "label": item.get("label") or _(item["table"])}
			for item in profile.get("tables") or []
		],
	}


def _adapter_ui(plugin, ctx):
	from frappe_tools.extractors.ui import capabilities
	return capabilities(plugin, ctx)


def _get_extraction(name, permission, *, for_update=False):
	doc = frappe.get_doc("Document Extraction", name, for_update=for_update)
	doc.check_permission(permission)
	if doc.target_doctype:
		frappe.has_permission(doc.target_doctype, "read", throw=True)
		if permission == "write":
			if doc.get("operation_mode") == "Attach Existing" and doc.get("existing_document"):
				if not frappe.has_permission(doc.target_doctype, doc=doc.existing_document, ptype="write"):
					frappe.throw(_("This document is not available."), frappe.PermissionError)
			else:
				frappe.has_permission(doc.target_doctype, "create", throw=True)
	return doc


def _verified_document_pages(extraction, file_url, page_no, remaining):
	file_doc = _verified_source_file(extraction, file_url)
	extension = os.path.splitext(file_doc.file_name or file_url)[1].lower()
	if extension in ALLOWED_IMAGE_EXTENSIONS:
		return [_verified_page_file(extraction, file_url, page_no, file_doc=file_doc)]
	if extension != ".pdf":
		frappe.throw(_("Unsupported document type {0}.").format(extension or "unknown"))
	content = file_doc.get_content()
	if len(content) > 15 * 1024 * 1024:
		frappe.throw(_("Each source document must be 15 MB or smaller."))
	pages = service.document_pages(content, extension, file_doc.file_name, max_pages=remaining)
	if len(pages) > remaining:
		frappe.throw(_("A maximum of 20 rendered pages is supported per extraction."))
	from frappe.utils.file_manager import save_file

	rows = []
	for offset, page in enumerate(pages):
		current = page_no + offset
		rendered = save_file(
			f"extract-{extraction}-p{current}.png",
			page["content"],
			"Document Extraction",
			extraction,
			is_private=1,
		)
		rows.append({
			"page_no": current,
			"image": rendered.file_url,
			"file_name": f"{file_doc.file_name} · page {offset + 1}",
			"width": page["width"],
			"height": page["height"],
		})
	return rows


def _verified_source_file(extraction, file_url):
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	if not file_doc.is_private:
		frappe.throw(_("OCR source files must be private."))
	if file_doc.attached_to_doctype != "Document Extraction" or file_doc.attached_to_name != extraction:
		frappe.throw(_("Uploaded file does not belong to this extraction."), frappe.PermissionError)
	extension = os.path.splitext(file_doc.file_name or file_url)[1].lower()
	if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
		frappe.throw(_("Unsupported document type {0}.").format(extension or "unknown"))
	if cint(file_doc.file_size) > 15 * 1024 * 1024:
		frappe.throw(_("Each source document must be 15 MB or smaller."))
	return file_doc


def _verified_page_file(extraction, file_url, page_no, file_doc=None):
	file_doc = file_doc or _verified_source_file(extraction, file_url)

	width = height = 0
	try:
		from PIL import Image, ImageOps

		with Image.open(file_doc.get_full_path()) as image:
			image.verify()
		with Image.open(file_doc.get_full_path()) as image:
			width, height = ImageOps.exif_transpose(image).size
	except Exception:
		frappe.throw(_("{0} is not a readable image.").format(file_doc.file_name))
	return {
		"page_no": page_no,
		"image": file_doc.file_url,
		"file_name": file_doc.file_name,
		"width": width,
		"height": height,
	}


def _field_rows(doc, header):
	by_name = {item.fieldname: item for item in doc.extracted_fields}
	rows = []
	for field in header:
		row = by_name.get(field["fieldname"])
		rows.append({
			**field,
			"value": row.value if row else "",
			"printed_value": row.llm_value if row else "",
			"raw_text": row.llm_raw_text if row else "",
			"confidence": flt(row.confidence) if row else 0,
			"status": row.status if row else "Missing",
			"source_page": cint(row.source_page) if row else 0,
			"bbox": _json_object(row.bbox_json) if row else None,
			"matched_value": row.matched_value if row else None,
		})
	return rows


def _table_rows(doc, table_specs):
	result = []
	for spec in table_specs:
		rows = []
		for line in sorted(doc.lines, key=lambda item: cint(item.row_no)):
			if (line.table or doc.line_table) != spec["table"]:
				continue
			values = _json_object(line.raw_json)
			cell_evidence = values.pop("_cell_evidence", {}) if isinstance(values.get("_cell_evidence"), dict) else {}
			values.pop("_invoice_candidates", None)
			values.pop("resolved_sales_invoices", None)
			for key, fallback in {
				"description": line.description,
				"supplier_code": line.supplier_code,
				"hsn": line.hsn,
				"qty": line.qty,
				"uom": line.uom,
				"rate": line.rate,
				"amount": line.amount,
			}.items():
				if key not in values and fallback not in (None, ""):
					values[key] = fallback
			rows.append({
				"table": spec["table"],
				"row_no": cint(line.row_no),
				"values": values,
				"source_page": cint(line.source_page),
				"bbox": _json_object(line.bbox_json),
				"cell_evidence": cell_evidence,
				"status": line.resolution_status,
				"confidence": flt(line.match_confidence),
				"matched_value": getattr(line, "matched_item", None),
				"candidates": _as_list(getattr(line, "candidates_json", None)),
				"master_proposal": getattr(line, "master_proposal", None),
			})
		result.append({
			"fieldname": spec["table"],
			"label": spec.get("label") or _(spec["table"]),
			"notes": spec.get("notes"),
			"resolver": spec.get("resolver"),
			"columns": spec.get("columns") or [],
			"rows": rows,
		})
	return result


def _review_issues(doc, fields, tables, plugin, ctx, *, include_document=True):
	if doc.status != "Review":
		return []
	issues = []
	require_evidence = pipeline.processing_config()["require_verified_evidence"]
	for field in fields:
		value = field.get("value")
		if field.get("required") and value in (None, ""):
			issues.append(_issue("error", f"field:{field['fieldname']}", _("{0} is required.").format(field["label"]), field))
		elif value in (None, ""):
			issues.append(_issue(
				"warning", f"field:{field['fieldname']}",
				_("No value was extracted for {0}. Enter it if it appears in the document.").format(field["label"]), field,
			))
		if field.get("link_doctype") and value and not frappe.db.exists(field["link_doctype"], value):
			issues.append(_issue("error", f"field:{field['fieldname']}", _("{0} must match an existing {1}.").format(field["label"], field["link_doctype"]), field))
		if value not in (None, "") and flt(field.get("confidence")) < 0.65 and field.get("status") == "Pending":
			issues.append(_issue("warning", f"field:{field['fieldname']}", _("Check the extracted value for {0}.").format(field["label"]), field))
		if require_evidence and field.get("input_source") != "local" and value not in (None, "") and not _has_verified_evidence(field.get("bbox")):
			issues.append(_issue(
				"error", f"field:{field['fieldname']}",
				_("Verify the source highlight for {0}.").format(field["label"]), field,
			))
		if value not in (None, "") and field.get("status") not in {"Approved", "Edited"}:
			issues.append(_issue(
				"error", f"field:{field['fieldname']}",
				_("Confirm {0} before creating the Frappe draft.").format(field["label"]), field,
			))

	for table in tables:
		if not table["rows"]:
			issues.append({
				"severity": "warning",
				"path": f"table:{table['fieldname']}",
				"message": _("No rows were extracted for {0}. Add them if they appear in the document.").format(table["label"]),
				"source_page": 0,
				"bbox": None,
			})
		for row in table["rows"]:
			if require_evidence and any(value not in (None, "") for value in row["values"].values()) and not _has_verified_evidence(row.get("bbox")):
				issues.append(_issue(
					"error", f"table:{table['fieldname']}:{row['row_no']}",
					_("Verify the source highlight for {0}, row {1}.").format(table["label"], row["row_no"]), row,
				))
			for column in table["columns"]:
				if column.get("required") and row["values"].get(column["key"]) in (None, ""):
					issues.append(_issue("error", f"table:{table['fieldname']}:{row['row_no']}:{column['key']}", _("{0}, row {1}: {2} is required.").format(table["label"], row["row_no"], column["label"]), row))
				value = row["values"].get(column["key"])
				cell = (row.get("cell_evidence") or {}).get(column["key"]) or {}
				# A row-level box is useful context, not proof of each extracted value.
				# The review client can now correct a specific column independently.
				if (require_evidence and column.get("evidence") and value not in (None, "")
					and not _has_verified_evidence(cell)):
					issues.append(_issue("error", f"table:{table['fieldname']}:{row['row_no']}:{column['key']}",
						_("Verify the source highlight for {0}, row {1}: {2}.").format(table["label"], row["row_no"], column["label"]),
						{"source_page": cell.get("page") or row.get("source_page"), "bbox": cell,
						 "table": table["fieldname"], "row_no": row["row_no"], "column": column["key"]}))
				if column.get("link_doctype") and value and not frappe.db.exists(column["link_doctype"], value):
					issues.append(_issue("error", f"table:{table['fieldname']}:{row['row_no']}:{column['key']}", _("{0}, row {1}: {2} must match an existing {3}.").format(table["label"], row["row_no"], column["label"], column["link_doctype"]), row))
			values = row["values"]
			if all(values.get(key) not in (None, "") for key in ("qty", "rate", "amount")):
				expected = flt(values["qty"]) * flt(values["rate"])
				if abs(expected - flt(values["amount"])) > 0.02:
					issues.append(_issue("error", f"table:{table['fieldname']}:{row['row_no']}:amount", _("{0}, row {1}: quantity × rate does not equal amount.").format(table["label"], row["row_no"]), row))
			if row.get("status") not in {"Confirmed", "Free Text"}:
				issues.append(_issue(
					"error", f"table:{table['fieldname']}:{row['row_no']}:review",
					_("{0}, row {1}: confirm this row before creating the Frappe draft.").format(table["label"], row["row_no"]), row,
				))

	if not include_document:
		return issues
	from frappe_tools.extractors import phases
	for message in phases.creation_issues(doc, plugin, ctx):
		issues.append({"severity": "error", "path": "document", "message": message, "source_page": 0, "bbox": None})
	validator = getattr(plugin, "validator", None) or plugin.validate
	for message in dict.fromkeys([*(validator(ctx, doc) or []), *revisions.creation_issues(doc)]):
		issues.append({"severity": "error", "path": "document", "message": str(message), "source_page": 0, "bbox": None})
	return issues


def _action_preview(doc, fields, tables, issues, workflow=None):
	"""Return the exact, read-only Frappe mutation proposed by this review."""
	blockers = [issue["message"] for issue in issues if issue["severity"] == "error"]
	blockers = list(dict.fromkeys([*blockers, *revisions.creation_issues(doc)]))
	workflow = workflow or {}
	is_update = getattr(doc, "operation_mode", None) == "Attach Existing"
	accepted_fields = [
		{"fieldname": field["fieldname"], "label": field["label"], "value": field.get("value")}
		for field in fields
		if field.get("status") in {"Approved", "Edited"} and field.get("value") not in (None, "")
	]
	accepted_tables = []
	for table in tables:
		rows = [
			{"row_no": row["row_no"], "values": row["values"]}
			for row in table["rows"] if row.get("status") in {"Confirmed", "Free Text"}
		]
		if rows:
			accepted_tables.append({"fieldname": table["fieldname"], "label": table["label"], "rows": rows})
	return {
		"kind": "update_reviewed" if is_update else "create_draft",
		"label": _("Save reviewed {0}").format(_(doc.target_doctype)) if is_update else workflow.get("action_label") or _("Create {0} draft").format(_(doc.target_doctype)),
		"description": workflow.get("action_description"),
		"result_description": workflow.get("result_description"),
		"target_doctype": doc.target_doctype,
		"document_status": "Draft",
		"fields": accepted_fields,
		"tables": accepted_tables,
		"field_count": len(accepted_fields),
		"row_count": sum(len(table["rows"]) for table in accepted_tables),
		"blockers": blockers,
		"ready": doc.status == "Review" and not blockers,
		"idempotent": True,
		"will_submit": False,
	}


def _issue(severity, path, message, source):
	parts = path.split(":")
	target = {}
	if parts[0] == "field" and len(parts) > 1:
		target = {"fieldname": parts[1]}
	elif parts[0] == "table" and len(parts) > 2:
		target = {"table": parts[1], "row_no": cint(parts[2]),
			"column": parts[3] if len(parts) > 3 and parts[3] != "review" else None}
	return {
		**target,
		"label": source.get("label") or (parts[-1] if len(parts) > 1 else "Document evidence"),
		"severity": severity,
		"path": path,
		"message": message,
		"source_page": cint(source.get("source_page")),
		"bbox": source.get("bbox"),
	}


def _has_verified_evidence(value):
	if not isinstance(value, dict):
		return False
	if value.get("source") not in {"ocr", "manual"} or value.get("verified") is False:
		return False
	return bool(pipeline.normalize_bbox(value))


def _stages(status, decision_phase=None):
	order = ["Received", "Preparing", "Classifying", "Ordering", "Extracting", "Matching", "Review", "Created"]
	active = {
		"Draft": 0,
		"Preparing": 1,
		"Classifying": 2,
		"Classification Handoff": 2,
		"Queued": 3,
		"Extracting": 3,
		"Layout Handoff": 3,
		"Review": 5 if decision_phase in DECISION_PENDING_PHASES else 6,
		"Created": 7,
		"Attached": 7,
		"Failed": 4,
	}.get(status, 0)
	return [
		{
			"key": key.lower(),
			"label": key,
			"state": "failed" if status == "Failed" and index == active else "done" if index < active or status in {"Created", "Attached"} else "active" if index == active else "pending",
		}
		for index, key in enumerate(order)
	]


def _mirror_common_line_values(line, data):
	for key in ("description", "supplier_code", "hsn", "uom"):
		if key in data:
			setattr(line, key, data.get(key) or "")
	for key in ("qty", "rate", "amount"):
		if key in data:
			setattr(line, key, flt(data.get(key)))


def _as_list(value):
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except (TypeError, ValueError):
			return []
	return value if isinstance(value, list) else []


def _queue_status(status, decision_phase=None):
	if status == "Review" and decision_phase in DECISION_PENDING_PHASES:
		return "pending"
	for group, values in QUEUE_STATUS_MAP.items():
		if status in values:
			return group
	return "draft"


def _empty_queue_counts():
	return {"all": 0, **{group: 0 for group in QUEUE_STATUS_MAP}}


def _empty_queue_result(status, start, page_length):
	return {
		"items": [], "counts": _empty_queue_counts(), "status": status,
		"start": max(cint(start), 0), "page_length": min(max(cint(page_length), 1), 100),
		"total": 0, "has_previous": False, "has_more": False, "filters": {},
	}


def _job_state(row):
	"""Resolve the durable document state against its actual RQ job registry."""
	queue_status = _queue_status(row.get("status"), row.get("decision_phase"))
	if queue_status != "pending":
		return {"state": "not_applicable", "label": _("Not processing")}
	job_id = None
	if row.get("status") == "Review" and row.get("decision_phase") in DECISION_PENDING_PHASES:
		job_id = row.get("decision_job_id")
	elif row.get("status") == "Classifying":
		job_id = row.get("classification_job_id")
	else:
		job_id = row.get("processing_job_id")
	if not job_id:
		return {"state": "stale", "label": _("Processing record is missing its job")}
	try:
		from frappe.utils.background_jobs import get_job_status

		status = get_job_status(job_id)
	except Exception:
		# Redis being unavailable is different from a confirmed missing job. Do
		# not present it as running or enable a duplicate retry.
		return {"state": "unavailable", "label": _("Job status is temporarily unavailable")}
	value = str(getattr(status, "value", status) or "").lower()
	if value in {"queued", "deferred", "scheduled"}:
		return {"state": "queued", "label": _("Waiting to process")}
	if value in {"started", "busy"}:
		return {"state": "started", "label": _("Processing")}
	if value in {"finished", "complete", "completed"}:
		return {"state": "finished", "label": _("Finishing the document")}
	if value in {"failed", "stopped", "canceled", "cancelled"}:
		return {"state": "failed", "label": _("Processing failed")}
	if status is None:
		return {"state": "stale", "label": _("Processing stopped before completion")}
	return {"state": value or "unavailable", "label": _("Checking processing status")}


def _as_dict(value):
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except (TypeError, ValueError):
			return {}
	return value if isinstance(value, dict) else {}


def _json_object(value):
	if not value:
		return {}
	if isinstance(value, dict):
		return value
	try:
		parsed = json.loads(value)
		return parsed if isinstance(parsed, dict) else {}
	except (TypeError, ValueError):
		return {}


def _public_reason_result(value):
	result = _json_object(value)
	return {
		"status": result.get("status"),
		"confidence": flt(result.get("confidence")),
		"reasons": [str(reason) for reason in result.get("reasons") or []][:20],
		"handoff_reasons": [str(reason) for reason in result.get("handoff_reasons") or []][:20],
	} if result else {}

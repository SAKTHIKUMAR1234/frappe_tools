"""Automatic document intake router for code-owned extraction adapters."""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from frappe_tools.extractors import get_plugin, registered_targets
from frappe_tools.extractors.context import ExtractionContext
from frappe_tools.utils import llm


MIN_CLASSIFICATION_CONFIDENCE = 0.85
MAX_CLASSIFICATION_PAGES = 3
CLASSIFICATION_TIMEOUT_SECONDS = 240

SYSTEM_PROMPT = (
	"You classify scanned business documents for a Frappe automation router. "
	"Choose only from the supplied target names. Do not extract or invent business data. "
	"If the pages are mixed, unreadable, incomplete, or ambiguous, return target_doctype null. "
	"Return exactly one JSON object."
)


def candidate_profiles(*, check_permissions=False):
	"""Return installed adapter targets that the current user may create and read."""
	profiles = []
	for target in registered_targets():
		if not frappe.db.exists("DocType", target):
			continue
		if check_permissions and not (
			frappe.has_permission(target, "read") and frappe.has_permission(target, "create")
		):
			continue
		plugin = get_plugin(target)
		profiles.append({
			"target_doctype": target,
			"description": plugin.classification_description(),
		})
	return profiles


def enqueue(extraction, *, enqueue_after_commit=True):
	"""Queue classification for a rendered extraction whose target is unknown."""
	doc = extraction if getattr(extraction, "doctype", None) == "Document Extraction" else frappe.get_doc(
		"Document Extraction", extraction
	)
	if doc.target_doctype:
		frappe.throw(_("Classification is only used when Target DocType is not selected."))
	if not any(page.image for page in doc.pages):
		frappe.throw(_("No rendered pages are available for classification."))
	job_id = f"document-classification::{doc.name}"
	doc.status = "Classifying"
	doc.classification_phase = "Queued"
	doc.classification_job_id = job_id
	doc.handoff_reason = None
	doc.error_log = None
	doc.add_processing_event(
		"Classification",
		"Queued",
		"Queued automatic document classification before adapter extraction.",
	)
	doc.flags.skip_auto_process = True
	doc.save(ignore_permissions=True)
	frappe.enqueue(
		"frappe_tools.extractors.classification.run",
		queue="long",
		timeout=CLASSIFICATION_TIMEOUT_SECONDS,
		extraction_name=doc.name,
		enqueue_after_commit=enqueue_after_commit,
		job_id=job_id,
		deduplicate=True,
	)
	return {"extraction": doc.name, "status": doc.status, "job_id": job_id}


def run(extraction_name):
	"""Classify pages, select one adapter safely, then enqueue detailed extraction."""
	doc = frappe.get_doc("Document Extraction", extraction_name)
	if doc.target_doctype or doc.status not in {"Classifying", "Classification Handoff"}:
		return {"extraction": doc.name, "status": doc.status, "skipped": True}
	try:
		profiles = candidate_profiles(check_permissions=True)
		if not profiles:
			frappe.throw(_("No installed document adapter is available to this user."))
		doc.status = "Classifying"
		doc.classification_phase = "Running"
		doc.add_processing_event("Classification", "Running", "Inspecting the document type.")
		doc.flags.skip_auto_process = True
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		from frappe_tools.extractors import pipeline

		images = [
			pipeline.file_to_data_url(page.image)
			for page in sorted(doc.pages, key=lambda row: cint(row.page_no))[:MAX_CLASSIFICATION_PAGES]
		]
		prompt = _classification_prompt(profiles, len(doc.pages))
		result = llm.call_vision(
			images,
			SYSTEM_PROMPT,
			prompt,
			extraction=doc.name,
			target_doctype="Document Extraction",
		)
		decision = _normalize_decision(result.get("data"), profiles)
		usage = result.get("usage") or {}
		doc.reload()
		doc.classification_model = result.get("model")
		doc.classification_confidence = decision["confidence"] * 100
		doc.classification_json = json.dumps(decision, ensure_ascii=False)
		doc.total_tokens = cint(doc.total_tokens) + cint(usage.get("total_tokens"))
		doc.cost_usd = flt(doc.cost_usd) + flt(usage.get("cost"))

		if not decision["accepted"]:
			doc.status = "Classification Handoff"
			doc.classification_phase = "Human Handoff"
			doc.handoff_reason = "; ".join(decision["handoff_reasons"])
			doc.processing_completed_on = now_datetime()
			doc.add_processing_event(
				"Classification",
				"Human Handoff",
				"The document type needs human confirmation before extraction.",
				decision,
			)
			doc.flags.skip_auto_process = True
			doc.save(ignore_permissions=True)
			frappe.db.commit()
			return {"extraction": doc.name, "status": doc.status, "classification": decision}

		_configure_target(doc, decision["target_doctype"])
		doc.classification_phase = "Classified"
		doc.handoff_reason = None
		doc.add_processing_event(
			"Classification",
			"Classified",
			f"Selected the {doc.target_doctype} adapter.",
			decision,
		)
		doc.flags.skip_auto_process = True
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		queued = pipeline.enqueue_extraction(doc.name, len(doc.pages), enqueue_after_commit=False)
		frappe.db.set_value("Document Extraction", doc.name, {
			"status": "Queued",
			"processing_job_id": queued["job_id"],
			"processing_completed_on": None,
		}, update_modified=False)
		frappe.db.commit()
		return {
			"extraction": doc.name,
			"status": "Queued",
			"classification": decision,
			**queued,
		}
	except Exception as exc:
		frappe.db.rollback()
		failed = frappe.get_doc("Document Extraction", extraction_name)
		failed.status = "Failed"
		failed.classification_phase = "Failed"
		failed.processing_completed_on = now_datetime()
		failed.error_log = f"{exc}\n\n{frappe.get_traceback()}"[:14000]
		failed.add_processing_event("Classification", "Failed", str(exc)[:500])
		failed.flags.skip_auto_process = True
		failed.save(ignore_permissions=True)
		frappe.db.commit()
		return {"extraction": failed.name, "status": failed.status, "error": str(exc)}


def accept_target(extraction_name, target_doctype):
	"""Apply a human classification override and continue the same durable run."""
	doc = frappe.get_doc("Document Extraction", extraction_name)
	doc.check_permission("write")
	if doc.target_doctype or doc.status not in {"Classification Handoff", "Failed"}:
		frappe.throw(_("This run is not waiting for document-type confirmation."))
	if target_doctype not in {row["target_doctype"] for row in candidate_profiles(check_permissions=True)}:
		frappe.throw(_("Target DocType {0} is not an available document adapter.").format(target_doctype))
	_configure_target(doc, target_doctype)
	doc.classification_phase = "Human Confirmed"
	doc.classification_override_by = frappe.session.user
	doc.classification_override_on = now_datetime()
	doc.handoff_reason = None
	doc.error_log = None
	doc.add_processing_event(
		"Classification",
		"Human Confirmed",
		f"{frappe.session.user} selected the {target_doctype} adapter.",
	)
	doc.flags.skip_auto_process = True
	doc.save()
	frappe.db.commit()
	from frappe_tools.extractors import pipeline

	queued = pipeline.enqueue_extraction(doc.name, len(doc.pages), enqueue_after_commit=False)
	frappe.db.set_value("Document Extraction", doc.name, {
		"status": "Queued",
		"processing_job_id": queued["job_id"],
		"processing_completed_on": None,
	}, update_modified=False)
	frappe.db.commit()
	return {"extraction": doc.name, "status": "Queued", **queued}


def _classification_prompt(profiles, page_count):
	return (
		f"This upload contains {page_count} page(s). Decide whether every page belongs to one supported "
		"document and choose its target. Supported targets:\n"
		+ json.dumps(profiles, ensure_ascii=False, indent=2)
		+ "\nReturn: {\"target_doctype\": <exact supplied name or null>, "
		"\"confidence\": <0..1>, \"document_category\": <short visual/layout family or null>, "
		"\"reasons\": [<brief evidence>], \"handoff_reasons\": [<ambiguities>]}. "
		"Use null and handoff when multiple document kinds are mixed or the type is uncertain."
	)


def _normalize_decision(value, profiles):
	value = value if isinstance(value, dict) else {}
	allowed = {row["target_doctype"] for row in profiles}
	target = value.get("target_doctype")
	confidence = max(0.0, min(flt(value.get("confidence")), 1.0))
	reasons = [str(item)[:300] for item in value.get("reasons") or []]
	handoff = [str(item)[:300] for item in value.get("handoff_reasons") or []]
	if target not in allowed:
		handoff.append("unsupported_or_unresolved_document_type")
	if confidence < MIN_CLASSIFICATION_CONFIDENCE:
		handoff.append(f"classification_confidence_below_{MIN_CLASSIFICATION_CONFIDENCE:.2f}")
	return {
		"target_doctype": target if target in allowed else None,
		"confidence": confidence,
		"document_category": str(value.get("document_category") or "")[:140] or None,
		"reasons": reasons,
		"handoff_reasons": list(dict.fromkeys(handoff)),
		"accepted": target in allowed and confidence >= MIN_CLASSIFICATION_CONFIDENCE and not handoff,
	}


def _configure_target(doc, target_doctype):
	plugin = get_plugin(target_doctype)
	tables = plugin.schema(ExtractionContext(target_doctype)).get("tables") or []
	doc.target_doctype = target_doctype
	doc.adapter_id = plugin.adapter_id()
	doc.line_table = tables[0]["table"] if tables else None

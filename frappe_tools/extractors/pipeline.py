"""The extraction engine — runs the fixed pipeline, delegating specifics to a plugin.

Stages: schema → LLM vision → parse (header + N tables) → resolve → [build: header +
tables 1:1 → transform → customize → insert]. The engine owns the mechanics; the
plugin contributes schema/resolve/transform/customize/validate.
"""

import base64
import io
import json
import math

import frappe
from frappe.utils import cint, flt, now_datetime

from frappe_tools.extractors import get_plugin
from frappe_tools.extractors import grounding
from frappe_tools.extractors import schema as S
from frappe_tools.extractors.context import ExtractionContext
from frappe_tools.utils import coerce, llm

REALTIME_EVENT = "frappe_tools_extraction"
SETTINGS_DOCTYPE = "Document Extraction Settings"
DEFAULT_TIMEOUT_PER_PAGE_MINUTES = 3
ALLOWED_BACKGROUND_QUEUES = {"short", "default", "long"}
MIN_BBOX_SIDE = 0.002
MIN_BBOX_PIXELS = 3
AUTO_APPROVE_CONFIDENCE = 0.90

SYSTEM_PROMPT = (
	"You are a meticulous document data-extraction engine. You read scanned business "
	"documents (one or more page images) and extract structured field values that strictly "
	"match a provided target schema, guided by the provided rule books. You never invent data: "
	"if a field is not present, return null. For every value you also report the exact raw text "
	"as printed, a calibrated confidence, the 1-based page index, and a bounding box. Respond "
	"with ONE valid JSON object and nothing else."
)


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

def build_user_prompt(target_doctype, header, rule_books, tables, addendum=None):
	parts = [f"TARGET DOCTYPE: {target_doctype} ({frappe._(target_doctype)})"]
	parts.append("\nHEADER FIELD SCHEMA (extract values for these fieldnames only):\n" + json.dumps(header, indent=2, ensure_ascii=False))

	for spec in (tables or []):
		label = spec.get("label") or spec["table"]
		parts.append(f"\nCHILD TABLE '{spec['table']}' ({label}) — extract one object per printed row:\n"
		             + json.dumps(spec.get("columns") or [], indent=2, ensure_ascii=False))
		if spec.get("notes"):
			parts.append(f"Notes for '{spec['table']}': {spec['notes']}")

	if rule_books:
		parts.append("\nRULE BOOKS (apply all):")
		for i, book in enumerate(rule_books, 1):
			parts.append(f"\n--- Rule Book {i}: {book.get('title') or ''} ---")
			if book.get("instructions"):
				parts.append(book["instructions"].strip())
			for r in book.get("field_rules") or []:
				bits = [f"- {r['fieldname']}"]
				if r.get("instruction"):
					bits.append(f": {r['instruction']}")
				if r.get("example"):
					bits.append(f" (e.g. {r['example']})")
				if r.get("output_format"):
					bits.append(f" [format: {r['output_format']}]")
				if r.get("required"):
					bits.append(" [REQUIRED]")
				parts.append("".join(bits))

	if addendum:
		parts.append("\n" + addendum)

	parts.append(
		"\nGENERAL RULES:\n"
		"- Dates as YYYY-MM-DD; datetimes as YYYY-MM-DD HH:MM:SS.\n"
		"- Numbers/currency: digits only, no separators or symbols.\n"
		"- 'raw_text' = substring exactly as printed; 'bbox' = [ymin, xmin, ymax, xmax] integers on a 0-1000 scale; "
		"'page' = 1-based image index; 'confidence' = 0.0-1.0.\n"
		"- Only include header fields present on the document (always include required ones, null if absent)."
	)

	out_fields = '{"fieldname": "<from schema>", "value": <value-or-null>, "raw_text": "<printed>", "confidence": 0.0, "page": 1, "bbox": [ymin, xmin, ymax, xmax]}'
	out = '{\n  "fields": [' + out_fields + ']'
	if tables:
		blocks = []
		for spec in tables:
			claim = '{"value": <value-or-null>, "raw_text": "<printed>", "confidence": 0.0, "page": 1, "bbox": [ymin, xmin, ymax, xmax]}'
			keys = ", ".join(f'"{c["key"]}": {claim if c.get("evidence") else "<value-or-null>"}'
				for c in (spec.get("columns") or []))
			blocks.append(f'"{spec["table"]}": [{{{keys}, "page": 1, "bbox": [ymin, xmin, ymax, xmax]}}]')
		out += ',\n  "tables": {' + ", ".join(blocks) + '}'
	out += "\n}"
	parts.append("\nOUTPUT FORMAT:\n" + out)
	return "\n".join(parts)


# --------------------------------------------------------------------------
# Worker: extract + resolve
# --------------------------------------------------------------------------

def processing_config(settings=None):
	"""Return validated background-job settings with safe defaults.

	The settings DocType is intentionally the single source for browser uploads,
	legacy API uploads and custom bulk scripts.
	"""
	if settings is None:
		try:
			settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
		except Exception:
			settings = None
	get_setting = settings.get if isinstance(settings, dict) else lambda key, default=None: getattr(settings, key, default)
	minutes = cint(get_setting("timeout_per_page_minutes", 0)) or DEFAULT_TIMEOUT_PER_PAGE_MINUTES
	queue = str(get_setting("background_queue", "") or "long").strip().lower()
	if queue not in ALLOWED_BACKGROUND_QUEUES:
		queue = "long"
	return {
		"queue": queue,
		"timeout_per_page_minutes": max(minutes, 1),
		"require_verified_evidence": bool(cint(get_setting("require_verified_evidence", 1))),
	}


def processing_timeout_seconds(page_count, settings=None):
	"""Calculate the worker timeout as configured minutes × uploaded pages."""
	config = processing_config(settings)
	return max(cint(page_count), 1) * config["timeout_per_page_minutes"] * 60


def enqueue_extraction(extraction_name, page_count=None, enqueue_after_commit=True):
	"""Enqueue exactly one independently retryable job for one extraction."""
	if page_count is None:
		doc = frappe.get_doc("Document Extraction", extraction_name)
		page_count = len([page for page in doc.pages if page.image])
	config = processing_config()
	timeout = processing_timeout_seconds(page_count, config)
	job_id = f"document-extraction::{extraction_name}"
	frappe.enqueue(
		"frappe_tools.extractors.pipeline.run",
		queue=config["queue"],
		timeout=timeout,
		extraction_name=extraction_name,
		enqueue_after_commit=enqueue_after_commit,
		job_id=job_id,
		deduplicate=True,
	)
	return {
		"job_id": job_id,
		"queue": config["queue"],
		"page_count": max(cint(page_count), 1),
		"timeout_seconds": timeout,
	}

def run(extraction_name):
	doc = frappe.get_doc("Document Extraction", extraction_name)
	try:
		if doc.status not in {"Queued", "Extracting"}:
			return
		doc.status = "Extracting"
		doc.error_log = None
		if not doc.processing_started_on:
			doc.processing_started_on = now_datetime()
		doc.add_processing_event("Vision Extraction", "Extracting", "Vision extraction and evidence grounding started.")
		doc.flags.skip_auto_process = True
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		publish(doc.name, "Extracting")

		# A configured scanner layout defines both the document-package category
		# and its section order. Route/reorder before sending page images to the
		# extraction model so all downstream page references use logical order.
		from frappe_tools.extractors import layout_routing

		layout_result = layout_routing.prepare(doc)
		if not layout_result.get("ready"):
			publish(doc.name, "Layout Handoff")
			return
		if layout_result.get("required"):
			doc.reload()

		plugin = get_plugin(doc.target_doctype)
		if doc.get("operation_mode") == "Attach Existing" and not plugin.processes_attached_target():
			from frappe_tools.scan_lineage import finalize_extraction_target

			result = finalize_extraction_target(
				doc,
				doc.existing_document,
				status="Attached",
				stage="Direct Attachment",
			)
			scanned = result.get("scanned_document")
			if not scanned:
				raise ValueError("The categorized scan could not be linked to the existing document.")
			frappe.db.commit()
			publish(doc.name, "Attached")
			return

		ctx = ExtractionContext(doc.target_doctype)
		sch = plugin.schema(ctx)
		header = sch.get("header") or []
		tables = sch.get("tables") or []
		rule_books = plugin.instructions(ctx)
		prompt = build_user_prompt(doc.target_doctype, header, rule_books, tables, plugin.prompt_addendum(ctx))

		images = [file_to_data_url(p.image) for p in doc.pages if p.image]
		if not images:
			raise ValueError("No readable page images found for this extraction.")

		result = llm.call_vision(images, SYSTEM_PROMPT, prompt, extraction=doc.name, target_doctype=doc.target_doctype)
		data = result["data"]

		doc.set("extracted_fields", [])
		for row in result_to_rows(data, header):
			doc.append("extracted_fields", row)
		doc.set("lines", [])
		for row in result_to_lines(data, tables):
			doc.append("lines", row)

		# VLM coordinates are approximate. Ground extracted text to local OCR
		# word boxes before review; model boxes remain an explicit fallback.
		grounding.apply(doc)

		plugin.resolve(ctx, doc)
		apply_review_decisions(doc)

		usage = result.get("usage") or {}
		doc.model_used = result.get("model")
		# Automatic classification may already have consumed one vision call.
		# Keep the durable run total rather than replacing that earlier usage.
		doc.total_tokens = cint(doc.total_tokens) + cint(usage.get("total_tokens"))
		doc.cost_usd = flt(doc.cost_usd) + flt(usage.get("cost"))
		doc.status = "Review"
		doc.error_log = None
		doc.processing_completed_on = now_datetime()
		doc.add_processing_event("Review", "Review", "Extraction finished; resolved values are ready for review or human handoff.", {
			"model": doc.model_used,
			"fields": len(doc.extracted_fields),
			"lines": len(doc.lines),
		})
		doc.flags.skip_auto_process = True
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		publish(doc.name, "Review")
		if cint(doc.get("auto_decide")):
			try:
				from frappe_tools.automation.runtime import enqueue_decision

				enqueue_decision(doc.name, enqueue_after_commit=False)
				frappe.db.commit()
			except Exception as decision_error:
				# Vision evidence remains usable even when optional agent startup is
				# unavailable; fail this phase closed to human review, not extraction.
				frappe.db.rollback()
				review = frappe.get_doc("Document Extraction", extraction_name)
				review.decision_phase = "Human Handoff"
				review.handoff_reason = f"agent_queue_error: {str(decision_error)[:500]}"
				review.add_processing_event("Agent Decision", "Human Handoff", str(decision_error)[:500])
				review.flags.skip_auto_process = True
				review.save(ignore_permissions=True)
				frappe.db.commit()
				frappe.log_error(frappe.get_traceback(), f"Document decision queue failed: {extraction_name}")
	except Exception as exc:
		frappe.db.rollback()
		tb = frappe.get_traceback()
		frappe.log_error(tb, f"Document Extraction failed: {extraction_name}")
		failed = frappe.get_doc("Document Extraction", extraction_name)
		failed.status = "Failed"
		failed.processing_completed_on = now_datetime()
		failed.error_log = f"{exc}\n\n{tb}"[:14000]
		failed.add_processing_event("Extraction", "Failed", str(exc)[:500])
		failed.flags.skip_auto_process = True
		failed.save(ignore_permissions=True)
		frappe.db.commit()
		publish(extraction_name, "Failed", error=str(exc))


def result_to_rows(data, header):
	by_name = {}
	fields = (data or {}).get("fields")
	if isinstance(fields, list):
		for item in fields:
			if isinstance(item, dict) and item.get("fieldname"):
				by_name[item["fieldname"]] = item
	elif isinstance(fields, dict):
		by_name = {k: {**v, "fieldname": k} for k, v in fields.items() if isinstance(v, dict)}

	schema_by_name = {s["fieldname"]: s for s in header}
	rows, seen = [], set()
	for fieldname, item in by_name.items():
		s = schema_by_name.get(fieldname)
		if not s:
			continue
		seen.add(fieldname)
		rows.append(_row(s, item))
	for s in header:
		if s.get("required") and s["fieldname"] not in seen:
			rows.append(_row(s, {}))
	return rows


def _row(schema_field, item):
	fieldtype = schema_field["fieldtype"]
	raw_value = item.get("value")
	if fieldtype in S.LINK_FIELDTYPES:
		coerced = ""
	else:
		c = coerce.coerce_value(fieldtype, raw_value)
		coerced = "" if c is None else str(c)
	bbox = normalize_bbox(item.get("bbox"))
	return {
		"fieldname": schema_field["fieldname"],
		"label": schema_field.get("label"),
		"fieldtype": fieldtype,
		"value": coerced,
		"llm_value": "" if raw_value is None else str(raw_value),
		"llm_raw_text": (item.get("raw_text") or "")[:1000],
		"confidence": flt(item.get("confidence")),
		"bbox_json": json.dumps(bbox) if bbox else None,
		"source_page": cint(item.get("page")) or 1,
		"status": "Pending",
	}


def result_to_lines(data, tables):
	tables_data = (data or {}).get("tables")
	if not isinstance(tables_data, dict):
		legacy = (data or {}).get("lines")
		tables_data = {tables[0]["table"]: legacy} if (isinstance(legacy, list) and tables) else {}

	rows, n = [], 0
	for spec in (tables or []):
		table = spec["table"]
		matched_doctype = (spec.get("resolver") or {}).get("link_doctype")
		items = tables_data.get(table) or []
		if not isinstance(items, list):
			continue
		for item in items:
			if not isinstance(item, dict):
				continue
			n += 1
			normalized = dict(item)
			cell_evidence = {}
			for column in spec.get("columns") or []:
				key = column.get("key")
				claim = item.get(key)
				if not isinstance(claim, dict) or "value" not in claim:
					continue
				normalized[key] = claim.get("value")
				cell_evidence[key] = {"raw_text": claim.get("raw_text"), "confidence": flt(claim.get("confidence")),
					"page": cint(claim.get("page")) or cint(item.get("page")) or 1,
					"bbox": normalize_bbox(claim.get("bbox"))}
			if cell_evidence:
				normalized["_cell_evidence"] = cell_evidence
			bbox = normalize_bbox(item.get("bbox"))
			rows.append({
				"table": table, "row_no": n,
				"description": str(normalized.get("description") or normalized.get("item_name") or "")[:1000],
				"supplier_code": str(normalized.get("supplier_code") or "")[:140],
				"hsn": str(normalized.get("hsn") or normalized.get("gst_hsn_code") or "")[:30],
				"qty": flt(normalized.get("qty")), "uom": str(normalized.get("uom") or "")[:50],
				"rate": flt(normalized.get("rate")), "amount": flt(normalized.get("amount")),
				"raw_json": json.dumps(normalized),
				"source_page": cint(item.get("page")) or 1,
				"bbox_json": json.dumps(bbox) if bbox else None,
				"resolution_status": "Unmatched",
				"matched_doctype": matched_doctype,
				"match_confidence": flt(item.get("confidence")),
			})
	return rows


def apply_review_decisions(doc):
	"""Auto-accept only high-confidence values backed by verified local evidence.

	Anything doubtful remains Pending/Unmatched and is handed to the reviewer.
	This keeps the common path touch-free without allowing model confidence alone
	to authorize a Frappe mutation.
	"""
	field_policy = {}
	if getattr(doc, "target_doctype", None):
		try:
			field_policy = {row["fieldname"]: row for row in S.build_header_schema(doc.target_doctype)}
		except Exception:
			field_policy = {}
	for field in doc.extracted_fields:
		if field.status != "Pending" or field.value in (None, ""):
			continue
		policy = field_policy.get(getattr(field, "fieldname", None)) or {}
		threshold = flt(policy.get("minimum_confidence")) or AUTO_APPROVE_CONFIDENCE
		if policy.get("auto_approve", True) and flt(field.confidence) >= threshold and _verified_evidence(field.bbox_json):
			field.status = "Approved"
	for line in doc.lines:
		if line.resolution_status != "Matched":
			continue
		if flt(line.match_confidence) >= AUTO_APPROVE_CONFIDENCE and _verified_evidence(line.bbox_json):
			line.resolution_status = "Confirmed"


def _verified_evidence(raw):
	try:
		box = json.loads(raw) if isinstance(raw, str) else raw
	except (TypeError, ValueError):
		return False
	return bool(
		isinstance(box, dict)
		and box.get("source") in {"ocr", "manual"}
		and box.get("verified") is not False
		and normalize_bbox(box)
	)


# --------------------------------------------------------------------------
# Build the target document (general; plugin hooks transform + customize)
# --------------------------------------------------------------------------

def build(extraction_name):
	from frappe_tools.automation.revisions import creation_issues

	# Lock and reload both the parent and review rows; never build from an earlier
	# HTTP snapshot while a different request is saving review corrections.
	doc = frappe.get_doc("Document Extraction", extraction_name, for_update=True)
	if doc.created_document and frappe.db.exists(doc.target_doctype, doc.created_document):
		return doc.created_document
	if doc.status != "Review":
		frappe.throw("This extraction is not ready for document creation.")
	plugin = get_plugin(doc.target_doctype)
	ctx = ExtractionContext(doc.target_doctype)
	from frappe_tools.extractors import phases
	acceptance_issues = creation_issues(doc) + validate_review_acceptance(doc) + phases.creation_issues(doc, plugin, ctx)
	if acceptance_issues:
		frappe.throw("<br>".join(acceptance_issues))

	issues = plugin.validator(ctx, doc)
	if issues:
		frappe.throw("<br>".join(issues))

	target = plugin.target_for_build(ctx, doc)
	if target.doctype != doc.target_doctype:
		frappe.throw(frappe._("The document adapter returned an invalid target."))
	is_new = target.is_new()
	if not is_new:
		target.check_permission("write")
	virtual_fields = {field["fieldname"] for field in plugin.schema(ctx).get("header") or [] if field.get("virtual")}
	for f in doc.extracted_fields:
		if f.fieldname in virtual_fields or f.status not in {"Approved", "Edited"} or f.value in (None, ""):
			continue
		target.set(f.fieldname, coerce.coerce_value(f.fieldtype, f.value))

	build_rows = collect_build_rows(doc)
	plugin.writer(ctx, doc, build_rows)
	for table, rows in build_rows.items():
		for child in rows:
			if child:
				target.append(table, child)

	plugin.customize(ctx, target, doc)
	savepoint = "document_build_" + frappe.generate_hash(length=10)
	frappe.db.savepoint(savepoint)
	try:
		if is_new:
			target.insert()  # save only — never submit
		else:
			target.save()  # adapter-owned staging update; never submit
		plugin.after_insert(ctx, target, doc)
	except Exception:
		# A caller catching validation errors must not accidentally commit a PI
		# whose calculated accounting totals failed the adapter's final check.
		frappe.db.rollback(save_point=savepoint)
		raise
	return target.name


def validate_review_acceptance(doc):
	"""Reject every direct build attempt that still contains unreviewed data."""
	issues = []
	for field in doc.extracted_fields:
		if field.value not in (None, "") and field.status not in {"Approved", "Edited", "Rejected"}:
			issues.append(frappe._("{0} has not been reviewed.").format(field.label or field.fieldname))
	for line in doc.lines:
		if line.resolution_status not in {"Confirmed", "Free Text", "Rejected"}:
			issues.append(frappe._("Row {0} has not been reviewed.").format(line.row_no))
	return issues


def collect_build_rows(doc):
	meta = frappe.get_meta(doc.target_doctype)
	out, cache = {}, {}
	for l in doc.lines:
		table = l.table or doc.line_table
		if not table:
			continue
		cf = meta.get_field(table)
		if not cf or cf.fieldtype != "Table":
			continue
		if l.resolution_status not in {"Confirmed", "Free Text"}:
			continue
		if table not in cache:
			cm = frappe.get_meta(cf.options)
			cache[table] = (cm, S.primary_link_field(cm))
		cm, link_field = cache[table]
		row = _child_row(l, cm, link_field)
		if row:
			out.setdefault(table, []).append(row)
	return out


def _child_row(line, child_meta, link_field):
	data = {}
	if line.raw_json:
		try:
			data = json.loads(line.raw_json)
		except Exception:
			data = {}
	child = {}
	for key, val in data.items():
		if key in ("page", "bbox", "raw_text", "confidence"):
			continue
		if val in (None, ""):
			continue
		cdf = child_meta.get_field(key)
		if cdf and cdf.fieldtype in S.EXTRACTABLE_FIELDTYPES and cdf.fieldtype not in S.LINK_FIELDTYPES:
			child[key] = coerce.coerce_value(cdf.fieldtype, val)
	if line.matched_item and line.resolution_status != "Free Text" and link_field:
		child[link_field] = line.matched_item
	if not child.get(link_field) and child_meta.has_field("item_name") and not child.get("item_name") and line.description:
		child["item_name"] = line.description[:140]
	return child


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def normalize_bbox(raw):
	if not raw:
		return None
	if isinstance(raw, dict) and {"x", "y", "w", "h"} <= set(raw):
		box = {key: flt(raw[key]) for key in ("x", "y", "w", "h")}
	elif isinstance(raw, (list, tuple)) and len(raw) == 4:
		ymin, xmin, ymax, xmax = [flt(v) for v in raw]
		scale = 1000.0 if max(abs(ymin), abs(xmin), abs(ymax), abs(xmax)) > 1.5 else 1.0
		box = {"x": xmin / scale, "y": ymin / scale, "w": (xmax - xmin) / scale, "h": (ymax - ymin) / scale}
	else:
		return None
	if not all(math.isfinite(value) for value in box.values()):
		return None
	if box["w"] <= 0 or box["h"] <= 0:
		return None
	box["x"] = min(max(box["x"], 0.0), 1.0)
	box["y"] = min(max(box["y"], 0.0), 1.0)
	box["w"] = min(max(box["w"], 0.0), 1.0 - box["x"])
	box["h"] = min(max(box["h"], 0.0), 1.0 - box["y"])
	if box["w"] < MIN_BBOX_SIDE or box["h"] < MIN_BBOX_SIDE:
		return None
	return {k: round(v, 4) for k, v in box.items()}


def validate_bbox_for_page(raw, width, height):
	"""Normalize a box and reject regions too small to represent page text."""
	box = normalize_bbox(raw)
	width, height = cint(width), cint(height)
	if not box or width <= 0 or height <= 0:
		return None
	if box["w"] * width < MIN_BBOX_PIXELS or box["h"] * height < MIN_BBOX_PIXELS:
		return None
	return box


def save_page_image(data_url, extraction_name, page_no):
	if "," in data_url and data_url.startswith("data:"):
		header, b64 = data_url.split(",", 1)
		mime = header.split(";")[0].split(":")[1] if ":" in header else "image/jpeg"
		ext = (mime.split("/")[-1] or "jpg").lower()
	else:
		b64, ext = data_url, "jpg"
	content = base64.b64decode(b64)
	width = height = 0
	try:
		from PIL import Image
		with Image.open(io.BytesIO(content)) as img:
			width, height = img.size
	except Exception:
		pass
	from frappe.utils.file_manager import save_file
	file_doc = save_file(f"extract-{extraction_name}-p{page_no}.{ext}", content, "Document Extraction", extraction_name, is_private=1)
	return file_doc.file_url, width, height


def file_to_data_url(file_url):
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	content = file_doc.get_content()
	ext = (file_url.rsplit(".", 1)[-1] or "jpeg").lower()
	mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
	return f"data:{mime};base64,{base64.b64encode(content).decode()}"


def publish(extraction, status, error=None):
	frappe.publish_realtime(REALTIME_EVENT, {"extraction": extraction, "status": status, "error": error}, user=frappe.session.user)

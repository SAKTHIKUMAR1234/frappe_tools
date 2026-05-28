"""The extraction engine — runs the fixed pipeline, delegating specifics to a plugin.

Stages: schema → LLM vision → parse (header + N tables) → resolve → [build: header +
tables 1:1 → transform → customize → insert]. The engine owns the mechanics; the
plugin contributes schema/resolve/transform/customize/validate.
"""

import base64
import io
import json

import frappe
from frappe.utils import cint, flt

from frappe_tools.extractors import get_plugin
from frappe_tools.extractors import schema as S
from frappe_tools.extractors.context import ExtractionContext
from frappe_tools.utils import coerce, llm

REALTIME_EVENT = "frappe_tools_extraction"

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
			keys = ", ".join(f'"{c["key"]}": <value-or-null>' for c in (spec.get("columns") or []))
			blocks.append(f'"{spec["table"]}": [{{{keys}, "page": 1, "bbox": [ymin, xmin, ymax, xmax]}}]')
		out += ',\n  "tables": {' + ", ".join(blocks) + '}'
	out += "\n}"
	parts.append("\nOUTPUT FORMAT:\n" + out)
	return "\n".join(parts)


# --------------------------------------------------------------------------
# Worker: extract + resolve
# --------------------------------------------------------------------------

def run(extraction_name):
	doc = frappe.get_doc("Document Extraction", extraction_name)
	try:
		plugin = get_plugin(doc.target_doctype)
		ctx = ExtractionContext(doc.target_doctype)
		sch = plugin.schema(ctx)
		header = sch.get("header") or []
		tables = sch.get("tables") or []
		rule_books = S.get_rule_books(doc.target_doctype)
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

		plugin.resolve(ctx, doc)

		usage = result.get("usage") or {}
		doc.model_used = result.get("model")
		doc.total_tokens = cint(usage.get("total_tokens"))
		doc.cost_usd = flt(usage.get("cost"))
		doc.status = "Review"
		doc.error_log = None
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		publish(doc.name, "Review")
	except Exception as exc:
		frappe.db.rollback()
		tb = frappe.get_traceback()
		frappe.log_error(tb, f"Document Extraction failed: {extraction_name}")
		failed = frappe.get_doc("Document Extraction", extraction_name)
		failed.status = "Failed"
		failed.error_log = f"{exc}\n\n{tb}"[:14000]
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
		items = tables_data.get(table) or []
		if not isinstance(items, list):
			continue
		for item in items:
			if not isinstance(item, dict):
				continue
			n += 1
			bbox = normalize_bbox(item.get("bbox"))
			rows.append({
				"table": table, "row_no": n,
				"description": str(item.get("description") or item.get("item_name") or "")[:1000],
				"supplier_code": str(item.get("supplier_code") or "")[:140],
				"hsn": str(item.get("hsn") or item.get("gst_hsn_code") or "")[:30],
				"qty": flt(item.get("qty")), "uom": str(item.get("uom") or "")[:50],
				"rate": flt(item.get("rate")), "amount": flt(item.get("amount")),
				"raw_json": json.dumps(item)[:4000],
				"source_page": cint(item.get("page")) or 1,
				"bbox_json": json.dumps(bbox) if bbox else None,
				"resolution_status": "Unmatched",
			})
	return rows


# --------------------------------------------------------------------------
# Build the target document (general; plugin hooks transform + customize)
# --------------------------------------------------------------------------

def build(extraction_name):
	doc = frappe.get_doc("Document Extraction", extraction_name)
	plugin = get_plugin(doc.target_doctype)
	ctx = ExtractionContext(doc.target_doctype)

	issues = plugin.validate(ctx, doc)
	if issues:
		frappe.throw("<br>".join(issues))

	target = frappe.new_doc(doc.target_doctype)
	for f in doc.extracted_fields:
		if f.status == "Rejected" or f.value in (None, ""):
			continue
		target.set(f.fieldname, coerce.coerce_value(f.fieldtype, f.value))

	build_rows = collect_build_rows(doc)
	plugin.transform(ctx, doc, build_rows)
	for table, rows in build_rows.items():
		for child in rows:
			if child:
				target.append(table, child)

	plugin.customize(ctx, target, doc)
	target.insert()  # save only — never submit
	return target.name


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
		if l.resolution_status == "Rejected":
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
		box = {k: flt(raw[k]) for k in ("x", "y", "w", "h")}
	elif isinstance(raw, (list, tuple)) and len(raw) == 4:
		ymin, xmin, ymax, xmax = [flt(v) for v in raw]
		scale = 1000.0 if max(abs(ymin), abs(xmin), abs(ymax), abs(xmax)) > 1.5 else 1.0
		box = {"x": xmin / scale, "y": ymin / scale, "w": (xmax - xmin) / scale, "h": (ymax - ymin) / scale}
	else:
		return None
	box["x"] = min(max(box["x"], 0.0), 1.0)
	box["y"] = min(max(box["y"], 0.0), 1.0)
	box["w"] = min(max(box["w"], 0.0), 1.0 - box["x"])
	box["h"] = min(max(box["h"], 0.0), 1.0 - box["y"])
	return {k: round(v, 4) for k, v in box.items()}


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

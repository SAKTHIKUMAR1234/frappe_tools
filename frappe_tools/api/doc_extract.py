"""Document extraction pipeline: scan -> rule books + LLM -> resolve -> review -> create.

The GENERAL engine works for every DocType with zero custom code: it extracts the
header fields + any number of declared child tables, resolves Link values, and
BUILDS + saves the target document itself. A per-(system, doctype) adapter only
overrides specifics through thin hooks (resolve / transform / customize_document);
it never reimplements this pipeline.
"""

import base64
import io
import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from frappe_tools.adapters import get_adapter
from frappe_tools.utils import coerce, llm

REALTIME_EVENT = "frappe_tools_extraction"

EXTRACTABLE_FIELDTYPES = {
	"Data", "Small Text", "Text", "Long Text", "Text Editor",
	"Select", "Link", "Dynamic Link",
	"Date", "Datetime", "Time",
	"Int", "Float", "Currency", "Percent",
	"Check", "Phone",
}
LINK_FIELDTYPES = {"Link", "Dynamic Link"}


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

@frappe.whitelist()
def get_extractable_doctypes():
	names = frappe.get_all("Document Rule Book", filters={"enabled": 1}, distinct=True, pluck="target_doctype")
	return [{"doctype": n, "label": _(n)} for n in names if n]


def build_target_schema(target_doctype):
	"""Generic scalar header schema derived from DocType meta."""
	meta = frappe.get_meta(target_doctype)
	schema = []
	for df in meta.fields:
		if df.fieldtype not in EXTRACTABLE_FIELDTYPES:
			continue
		if df.hidden or df.read_only or getattr(df, "is_virtual", 0):
			continue
		if df.fieldname.startswith("_"):
			continue
		entry = {"fieldname": df.fieldname, "label": df.label or df.fieldname, "fieldtype": df.fieldtype, "required": bool(df.reqd)}
		if df.fieldtype == "Select" and df.options:
			entry["options"] = [o for o in (df.options or "").split("\n") if o != ""]
		elif df.fieldtype == "Link" and df.options:
			entry["link_doctype"] = df.options
		if df.description:
			entry["description"] = df.description
		schema.append(entry)
	return schema


def header_schema(target_doctype, adapter):
	if adapter and adapter.header_fields():
		return adapter.header_fields()
	return build_target_schema(target_doctype)


def rulebook_tables(target_doctype):
	"""Child table fieldnames the rule books declare (multi), with line_table fallback."""
	out, seen = [], set()
	for name in frappe.get_all("Document Rule Book", filters={"target_doctype": target_doctype, "enabled": 1},
	                           order_by="priority desc, modified asc", pluck="name"):
		rb = frappe.get_doc("Document Rule Book", name)
		for t in (rb.tables or []):
			if t.table_fieldname and t.table_fieldname not in seen:
				seen.add(t.table_fieldname)
				out.append({"table": t.table_fieldname, "label": t.label, "notes": t.notes})
		if rb.line_table and rb.line_table not in seen:  # legacy single-table fallback
			seen.add(rb.line_table)
			out.append({"table": rb.line_table, "label": None, "notes": None})
	return out


def _table_columns_from_meta(target_doctype, table_fieldname):
	meta = frappe.get_meta(target_doctype)
	cf = meta.get_field(table_fieldname)
	if not cf or cf.fieldtype != "Table":
		return []
	cm = frappe.get_meta(cf.options)
	cols = []
	for df in cm.fields:
		if df.fieldtype not in EXTRACTABLE_FIELDTYPES:
			continue
		if df.hidden or df.read_only or getattr(df, "is_virtual", 0):
			continue
		if df.fieldname.startswith("_"):
			continue
		col = {"key": df.fieldname, "label": df.label or df.fieldname, "type": df.fieldtype}
		if df.fieldtype == "Link" and df.options:
			col["link_doctype"] = df.options
		cols.append(col)
	return cols[:20]


def tables_spec(target_doctype, adapter):
	"""[{table, label, notes?, columns:[...]}] — adapter curation wins, else rule book + meta."""
	if adapter and adapter.tables():
		return adapter.tables()
	spec = []
	for t in rulebook_tables(target_doctype):
		spec.append({"table": t["table"], "label": t.get("label"), "notes": t.get("notes"),
		             "columns": _table_columns_from_meta(target_doctype, t["table"])})
	return spec


def get_rule_books(target_doctype):
	names = frappe.get_all(
		"Document Rule Book",
		filters={"target_doctype": target_doctype, "enabled": 1},
		order_by="priority desc, modified asc",
		pluck="name",
	)
	books = []
	for name in names:
		doc = frappe.get_doc("Document Rule Book", name)
		books.append({
			"title": doc.title,
			"instructions": doc.instructions,
			"field_rules": [
				{"fieldname": r.fieldname, "label": r.label, "instruction": r.instruction,
				 "example": r.example, "output_format": r.output_format, "required": bool(r.required)}
				for r in (doc.field_rules or [])
			],
		})
	return books


# --------------------------------------------------------------------------
# Prompt building
# --------------------------------------------------------------------------

SYSTEM_PROMPT = (
	"You are a meticulous document data-extraction engine. You read scanned business "
	"documents (one or more page images) and extract structured field values that strictly "
	"match a provided target schema, guided by the provided rule books. You never invent data: "
	"if a field is not present, return null. For every value you also report the exact raw text "
	"as printed, a calibrated confidence, the 1-based page index, and a bounding box. Respond "
	"with ONE valid JSON object and nothing else."
)


def build_user_prompt(target_doctype, schema, rule_books, ts=None, addendum=None):
	parts = [f"TARGET DOCTYPE: {target_doctype} ({_(target_doctype)})"]
	parts.append("\nHEADER FIELD SCHEMA (extract values for these fieldnames only):\n" + json.dumps(schema, indent=2, ensure_ascii=False))

	for spec in (ts or []):
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
	if ts:
		table_blocks = []
		for spec in ts:
			keys = ", ".join(f'"{c["key"]}": <value-or-null>' for c in (spec.get("columns") or []))
			table_blocks.append(f'"{spec["table"]}": [{{{keys}, "page": 1, "bbox": [ymin, xmin, ymax, xmax]}}]')
		out += ',\n  "tables": {' + ", ".join(table_blocks) + '}'
	out += "\n}"
	parts.append("\nOUTPUT FORMAT:\n" + out)
	return "\n".join(parts)


# --------------------------------------------------------------------------
# Extraction entry point + worker
# --------------------------------------------------------------------------

@frappe.whitelist()
def extract_document(target_doctype, images):
	llm.ensure_ready()
	if not frappe.has_permission(target_doctype, "create"):
		frappe.throw(_("You do not have permission to create {0}.").format(target_doctype), frappe.PermissionError)
	if not get_rule_books(target_doctype):
		frappe.throw(_("No enabled rule book exists for {0}. Create one first.").format(target_doctype))

	if isinstance(images, str):
		images = json.loads(images)
	if not images:
		frappe.throw(_("No scanned pages were provided."))

	adapter = get_adapter(target_doctype)
	ts = tables_spec(target_doctype, adapter)
	extraction = frappe.new_doc("Document Extraction")
	extraction.target_doctype = target_doctype
	extraction.status = "Extracting"
	extraction.line_table = ts[0]["table"] if ts else None  # primary table, for display
	extraction.insert()

	for idx, data_url in enumerate(images, 1):
		file_url, width, height = _save_page_image(data_url, extraction.name, idx)
		extraction.append("pages", {"page_no": idx, "image": file_url, "file_name": file_url.rsplit("/", 1)[-1], "width": width, "height": height})
	extraction.save()

	frappe.enqueue(
		"frappe_tools.api.doc_extract.run_extraction",
		queue="long", timeout=900, extraction=extraction.name, enqueue_after_commit=True,
	)
	return {"extraction": extraction.name, "status": extraction.status}


def run_extraction(extraction):
	doc = frappe.get_doc("Document Extraction", extraction)
	try:
		adapter = get_adapter(doc.target_doctype)
		schema = header_schema(doc.target_doctype, adapter)
		ts = tables_spec(doc.target_doctype, adapter)
		rule_books = get_rule_books(doc.target_doctype)
		user_prompt = build_user_prompt(doc.target_doctype, schema, rule_books, ts,
		                                adapter.prompt_addendum() if adapter else None)

		image_urls = [_file_to_data_url(p.image) for p in doc.pages if p.image]
		if not image_urls:
			raise ValueError("No readable page images found for this extraction.")

		result = llm.call_vision(image_urls, SYSTEM_PROMPT, user_prompt, extraction=doc.name, target_doctype=doc.target_doctype)
		data = result["data"]

		doc.set("extracted_fields", [])
		for row in _result_to_rows(data, schema, doc.pages):
			doc.append("extracted_fields", row)

		doc.set("lines", [])
		for row in _result_to_lines(data, ts):
			doc.append("lines", row)

		if adapter:
			adapter.resolve(doc)

		usage = result.get("usage") or {}
		doc.model_used = result.get("model")
		doc.total_tokens = cint(usage.get("total_tokens"))
		doc.cost_usd = flt(usage.get("cost"))
		doc.status = "Review"
		doc.error_log = None
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		_publish(doc.name, "Review")
	except Exception as exc:
		frappe.db.rollback()
		tb = frappe.get_traceback()
		frappe.log_error(tb, f"Document Extraction failed: {extraction}")
		failed = frappe.get_doc("Document Extraction", extraction)
		failed.status = "Failed"
		failed.error_log = f"{exc}\n\n{tb}"[:14000]
		failed.save(ignore_permissions=True)
		frappe.db.commit()
		_publish(extraction, "Failed", error=str(exc))


def _result_to_rows(data, schema, pages):
	by_name = {}
	fields = (data or {}).get("fields")
	if isinstance(fields, list):
		for item in fields:
			if isinstance(item, dict) and item.get("fieldname"):
				by_name[item["fieldname"]] = item
	elif isinstance(fields, dict):
		by_name = {k: {**v, "fieldname": k} for k, v in fields.items() if isinstance(v, dict)}

	page_dims = {p.page_no: (cint(p.width), cint(p.height)) for p in pages}
	schema_by_name = {s["fieldname"]: s for s in schema}

	rows, seen = [], set()
	for fieldname, item in by_name.items():
		s = schema_by_name.get(fieldname)
		if not s:
			continue
		seen.add(fieldname)
		rows.append(_build_row(s, item, page_dims))
	for s in schema:
		if s.get("required") and s["fieldname"] not in seen:
			rows.append(_build_row(s, {}, page_dims))
	return rows


def _build_row(schema_field, item, page_dims):
	fieldtype = schema_field["fieldtype"]
	raw_value = item.get("value")
	if fieldtype in LINK_FIELDTYPES:  # resolved separately; don't seed value with raw text
		coerced = ""
	else:
		c = coerce.coerce_value(fieldtype, raw_value)
		coerced = "" if c is None else str(c)
	bbox = _normalize_bbox(item.get("bbox"))
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


def _result_to_lines(data, ts):
	"""Parse data['tables'] = {tablename: [rows]} (legacy data['lines'] -> primary table).
	row_no is GLOBALLY unique across tables so it can key per-row review actions."""
	tables_data = (data or {}).get("tables")
	if not isinstance(tables_data, dict):
		legacy = (data or {}).get("lines")
		tables_data = {ts[0]["table"]: legacy} if (isinstance(legacy, list) and ts) else {}

	rows = []
	n = 0
	for spec in (ts or []):
		table = spec["table"]
		items = tables_data.get(table) or []
		if not isinstance(items, list):
			continue
		for item in items:
			if not isinstance(item, dict):
				continue
			n += 1
			bbox = _normalize_bbox(item.get("bbox"))
			rows.append({
				"table": table,
				"row_no": n,
				"description": str(item.get("description") or item.get("item_name") or "")[:1000],
				"supplier_code": str(item.get("supplier_code") or "")[:140],
				"hsn": str(item.get("hsn") or item.get("gst_hsn_code") or "")[:30],
				"qty": flt(item.get("qty")),
				"uom": str(item.get("uom") or "")[:50],
				"rate": flt(item.get("rate")),
				"amount": flt(item.get("amount")),
				"raw_json": json.dumps(item)[:4000],
				"source_page": cint(item.get("page")) or 1,
				"bbox_json": json.dumps(bbox) if bbox else None,
				"resolution_status": "Unmatched",
			})
	return rows


# --------------------------------------------------------------------------
# Review UI endpoints
# --------------------------------------------------------------------------

@frappe.whitelist()
def get_extraction(extraction):
	doc = frappe.get_doc("Document Extraction", extraction)
	doc.check_permission("read")
	schema_by_name = {s["fieldname"]: s for s in build_target_schema(doc.target_doctype)}

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

	declared = []
	seen = set()
	for l in lines:
		if l["table"] and l["table"] not in seen:
			seen.add(l["table"])
			declared.append({"table": l["table"], "label": _(l["table"])})

	return {
		"name": doc.name, "target_doctype": doc.target_doctype, "status": doc.status,
		"created_document": doc.created_document, "model_used": doc.model_used, "error_log": doc.error_log,
		"line_table": doc.line_table, "tables": declared, "pages": pages, "fields": fields, "lines": lines,
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


# --------------------------------------------------------------------------
# Document creation — GENERAL build (header + N child tables); adapter only hooks
# --------------------------------------------------------------------------

@frappe.whitelist()
def create_document_from_extraction(extraction):
	doc = frappe.get_doc("Document Extraction", extraction)
	doc.check_permission("write")
	if doc.created_document and frappe.db.exists(doc.target_doctype, doc.created_document):
		frappe.throw(_("A document ({0}) was already created from this extraction.").format(doc.created_document))

	adapter = get_adapter(doc.target_doctype)
	if adapter:
		issues = adapter.validate(doc)
		if issues:
			frappe.throw("<br>".join(issues))

	target = frappe.new_doc(doc.target_doctype)

	# Header fields (generic).
	for f in doc.extracted_fields:
		if f.status == "Rejected" or f.value in (None, ""):
			continue
		target.set(f.fieldname, coerce.coerce_value(f.fieldtype, f.value))

	# Child tables (generic) — built from each line's raw LLM row + resolved link.
	tables = _collect_build_rows(doc)
	if adapter:
		adapter.transform(doc, tables)  # reshape rows (e.g. consolidate to a common item)
	for table, rows in tables.items():
		for child in rows:
			if child:
				target.append(table, child)

	if adapter:
		adapter.customize_document(target, doc)  # tax overrides, app-aware tweaks

	target.insert()  # save only — never submit

	scanned = _create_scanned_document(doc, target.name)
	doc.created_document = target.name
	doc.scanned_document = scanned
	doc.status = "Created"
	doc.save()
	return {"doctype": doc.target_doctype, "docname": target.name}


def _collect_build_rows(doc):
	"""{table_fieldname: [child_row_dict, ...]} from the extracted lines."""
	meta = frappe.get_meta(doc.target_doctype)
	out = {}
	child_meta_cache = {}
	for l in doc.lines:
		table = l.table or doc.line_table
		if not table:
			continue
		cf = meta.get_field(table)
		if not cf or cf.fieldtype != "Table":
			continue
		if l.resolution_status == "Rejected":
			continue
		if table not in child_meta_cache:
			cm = frappe.get_meta(cf.options)
			child_meta_cache[table] = (cm, _primary_link_field(cm))
		cm, link_field = child_meta_cache[table]
		row = _build_child_row(l, cm, link_field)
		if row:
			out.setdefault(table, []).append(row)
	return out


def _build_child_row(line, child_meta, link_field):
	"""Map a line's raw LLM row onto child-table fields generically + resolved link."""
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
		if cdf and cdf.fieldtype in EXTRACTABLE_FIELDTYPES and cdf.fieldtype not in LINK_FIELDTYPES:
			child[key] = coerce.coerce_value(cdf.fieldtype, val)

	if line.matched_item and line.resolution_status != "Free Text" and link_field:
		child[link_field] = line.matched_item

	# Free-text row: ensure a name so mandatory item_name-style fields are satisfied.
	if not child.get(link_field) and child_meta.has_field("item_name") and not child.get("item_name") and line.description:
		child["item_name"] = line.description[:140]

	return child


def _primary_link_field(child_meta):
	for df in child_meta.fields:
		if df.fieldtype == "Link" and not df.hidden and not getattr(df, "is_virtual", 0):
			return df.fieldname
	return None


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


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _normalize_bbox(raw):
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


def _save_page_image(data_url, extraction_name, page_no):
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


def _file_to_data_url(file_url):
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	content = file_doc.get_content()
	ext = (file_url.rsplit(".", 1)[-1] or "jpeg").lower()
	mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
	return f"data:{mime};base64,{base64.b64encode(content).decode()}"


def _publish(extraction, status, error=None):
	frappe.publish_realtime(REALTIME_EVENT, {"extraction": extraction, "status": status, "error": error}, user=frappe.session.user)

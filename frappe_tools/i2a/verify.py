"""Deterministic verification, ERP cross-checks and prompt builders.

Layer order (engine step VERIFY):
  a) deterministic schema checks (free, always first)
  b) ERP cross-check — config-driven ground-truth lookups
  c) verify-model pass (prompts built here, called by the engine)

All functions are generic — the only domain input is the action's
output_schema and rules text (both UI config).
"""

import json
import re

import frappe

from frappe_tools.i2a import extract

COLLISION_IOU = 0.85
MAX_BBOX_AREA = 0.35  # a "value box" covering >35% of the page is never a tight value box
DETAIL_MAX = 200      # model-originated text embedded in deficiency details is capped


# ------------------------------------------------------------ shaping

def whitelist(extraction, schema):
	"""Filter a raw model extraction to exactly the schema keys.

	Returns (clean, dropped_keys). Every schema key is present in `clean`
	(missing ones become None for scalars / [] for arrays) so callers never
	see surprises. Each field entry is normalized to
	{"value","raw_text","confidence","bbox"} (arrays: list of those).
	"""
	clean, dropped = {}, []
	keys = {f["key"] for f in schema}

	for key in (extraction or {}):
		if key not in keys:
			dropped.append(key)

	for f in schema:
		key = f["key"]
		raw = (extraction or {}).get(key)
		if f.get("kind") == "array":
			items = raw if isinstance(raw, list) else ([raw] if raw else [])
			clean[key] = [_shape_item(i) for i in items if i is not None]
		else:
			clean[key] = _shape_item(raw) if raw is not None else None

	return clean, dropped


def _shape_item(item):
	if not isinstance(item, dict):
		item = {"value": item}
	return {
		"value": item.get("value"),
		"raw_text": item.get("raw_text") or (str(item.get("value")) if item.get("value") is not None else None),
		"confidence": item.get("confidence"),
		"bbox": extract.normalize_bbox(item.get("bbox")),
	}


def apply_formats(fields, schema):
	"""Normalize every value in place per its schema format.

	Returns a list of format deficiencies. Runs BEFORE any check so the
	gate always judges canonical values (review finding: never gate on
	pre-normalization values).
	"""
	deficiencies = []
	for f in schema:
		fmt = f.get("format")
		for idx, item in _iter_items(fields, f):
			if item is None or item.get("value") in (None, ""):
				continue
			normalized, ok = extract.normalize_value(item["value"], fmt, item.get("raw_text"))
			item["value"] = normalized
			if not ok:
				deficiencies.append(_deficiency(f, idx, "format", f"value {repr(item['value'])[:DETAIL_MAX]} fails format {fmt}"))
	return deficiencies


# ------------------------------------------------------------ checks

def deterministic_check(fields, schema):
	"""Required-present, bbox-present/sane, inter-field bbox collisions."""
	deficiencies = []

	for f in schema:
		items = list(_iter_items(fields, f))
		if f.get("required") and not any(i and i.get("value") not in (None, "") for _, i in items):
			deficiencies.append(_deficiency(f, None, "missing_value", "required field absent"))
			continue
		if f.get("bbox_required"):
			for idx, item in items:
				if item and item.get("value") not in (None, "") and not item.get("bbox"):
					deficiencies.append(_deficiency(f, idx, "bbox_missing", "value present without a bounding box"))

	# a box covering most of the page is a hallucinated fallback, not a value box
	for f in schema:
		for idx, item in _iter_items(fields, f):
			b = item.get("bbox") if item else None
			if b and b["w"] * b["h"] > MAX_BBOX_AREA:
				deficiencies.append(_deficiency(
					f, idx, "bbox_insane",
					f"bbox covers {round(b['w'] * b['h'] * 100)}% of the page — not a tight value box",
				))

	# collision: two different fields claiming (nearly) the same rectangle
	boxes = []
	for f in schema:
		for idx, item in _iter_items(fields, f):
			if item and item.get("bbox"):
				boxes.append((f["key"], idx, item["bbox"]))
	for i in range(len(boxes)):
		for j in range(i + 1, len(boxes)):
			ka, ia, ba = boxes[i]
			kb, ib, bb = boxes[j]
			if ka == kb:
				continue
			if extract.bbox_iou(ba, bb) >= COLLISION_IOU:
				deficiencies.append({
					"field": ka, "index": ia, "kind": "bbox_collision",
					"detail": f"bbox overlaps field {kb}[{ib}] (IoU >= {COLLISION_IOU})",
				})

	return deficiencies


def cross_check(fields, schema, context=None):
	"""Config-driven ERP ground-truth lookups.

	Schema per field: "cross_check": {"doctype": ..., "field": ...,
	"filters_template": {...}} — template values may reference
	"{<schema_key>}" (first value of that extracted field) or
	"{context.<key>}". A match marks the item cross_check="matched"
	(the honest 99.9% tier); a miss is a repairable deficiency.
	"""
	deficiencies = []
	context = context or {}

	for f in schema:
		spec = f.get("cross_check")
		if not spec or not spec.get("doctype") or not spec.get("field"):
			continue
		for idx, item in _iter_items(fields, f):
			if not item or item.get("value") in (None, ""):
				continue
			filters = _render_filters(spec.get("filters_template") or {}, fields, context)
			filters[spec["field"]] = item["value"]
			try:
				exists = frappe.get_all(spec["doctype"], filters=filters, limit=1, ignore_permissions=True)
			except Exception as exc:
				item["cross_check"] = "error"
				deficiencies.append(_deficiency(f, idx, "cross_check_error", str(exc)[:200]))
				continue
			if exists:
				item["cross_check"] = "matched"
			else:
				item["cross_check"] = "miss"
				deficiencies.append(_deficiency(
					f, idx, "cross_check_miss",
					f"no {spec['doctype']} found with {spec['field']} = {repr(item['value'])[:DETAIL_MAX]} — re-read the value from the image",
				))

	return deficiencies


def _render_filters(template, fields, context):
	rendered = {}
	for k, v in template.items():
		if isinstance(v, str):
			def sub(m):
				ref = m.group(1)
				if ref.startswith("context."):
					return str(context.get(ref[8:], ""))
				first = _first_value(fields, ref)
				return str(first if first is not None else "")
			v = re.sub(r"\{([\w.]+)\}", sub, v)
		rendered[k] = v
	return {k: v for k, v in rendered.items() if v not in ("", None)}


def _first_value(fields, key):
	entry = fields.get(key)
	if isinstance(entry, list):
		return entry[0]["value"] if entry else None
	return entry.get("value") if entry else None


# ------------------------------------------------------------ prompts

def build_extract_messages(action, image_parts, request_notes=None):
	system = _join(
		action.instructions,
		("DOMAIN KNOWLEDGE:\n" + action.knowledge) if action.knowledge else None,
		("REQUEST NOTES:\n" + request_notes) if request_notes else None,
		_schema_prompt(action),
	)
	return [
		{"role": "system", "content": system},
		{"role": "user", "content": [{"type": "text", "text": "Extract the fields from this document."}, *image_parts]},
	]


def build_verify_messages(action, image_parts, fields, crops=None, only=None):
	"""Claim-by-claim audit prompt, third-party framed.

	Research-backed shape (flow-tuning 2026-07-04): a holistic 'review this
	extraction' pass catches ~5% of real errors; models correct the SAME
	error 73-180% more when it is framed as ANOTHER system's output, checked
	field-by-field, and grounded with the claimed region's crop so the check
	is perception, not recall. Output contract is unchanged.

	crops: optional [{"field", "index", "part"}] — claimed-region image
	parts, appended after the full document and referenced per claim.
	only: optional {(field, index)} set — audit just those claims (delta
	re-verify after a repair round; round 0 always audits everything).
	"""
	crops = crops or []
	crop_no = {(c["field"], c.get("index")): n for n, c in enumerate(crops, 1)}

	claims, n = [], 0
	for f in action.parsed_schema():
		key = f["key"]
		entry = fields.get(key)
		items = entry if isinstance(entry, list) else [entry]
		for idx, item in enumerate(items):
			real_idx = idx if isinstance(entry, list) else None
			if only is not None and (key, real_idx) not in only:
				continue
			if item is None or item.get("value") in (None, ""):
				continue
			n += 1
			cn = crop_no.get((key, real_idx))
			claims.append(
				f'CLAIM {n} — field "{key}"'
				+ (f"[{real_idx}]" if real_idx is not None else "")
				+ (f' ({f["label"]})' if f.get("label") else "")
				+ f": it read {item.get('raw_text')!r} and recorded the value {item.get('value')!r}."
				+ (f" CROP {cn} shows the region it claims — read the crop's text and compare." if cn else "")
			)

	content = [{"type": "text", "text": "CLAIMS TO AUDIT:\n" + ("\n".join(claims) or "(no claims — nothing was extracted)")}]
	content += image_parts or []
	for cn, c in enumerate(crops, 1):
		content.append({"type": "text", "text": f'CROP {cn} (claimed region for field "{c["field"]}"):'})
		content.append(c["part"])
	return [{"role": "system", "content": verify_system(action)}, {"role": "user", "content": content}]


def verify_system(action):
	"""The auditor persona for the verifier's per-run session — sent once."""
	return _join(
		"You are an independent auditor. A PREVIOUS automated system (not you) extracted fields "
		"from the attached document. Its output is UNTRUSTED: verify each claim by READING the "
		"document image character by character — never by plausibility, never by trusting the claim.",
		"Work claim by claim, in order: locate the field on the document, read the printed text, "
		"compare with the recorded value. When a crop is provided, read the crop first, then "
		"confirm against the full document. In later turns, the document is the one already "
		"provided earlier in this conversation.",
		'Reply ONLY with JSON: {"disagreements": [{"field": <key>, "index": <int, arrays only>, '
		'"expected": <what the document actually shows>, "reason": <short>}]} — empty list when '
		"every claim matches.",
		("VERIFICATION RULES:\n" + action.rules) if action.rules else None,
	)


def build_verify_turn(action, image_parts, fields, crops=None, only=None):
	"""One verify USER turn for the session flow: claims text (+ the document
	image only on the FIRST turn — later turns rely on the session already
	holding it) + any new crops. Same claim/crop wording as the stateless
	builder so behaviour is unchanged."""
	return build_verify_messages(action, image_parts or [], fields, crops=crops, only=only)[1]["content"]


def build_som_messages(entries):
	"""Set-of-Marks selection: per field, a marked image with numbered red
	candidate boxes — the model picks a box number, never emits coordinates.

	entries: [{"field", "index", "label", "value", "raw_text",
	           "marks": [{"n", "text"}], "part": <marked image part>}]
	"""
	system = (
		"You locate printed values on a document. For each field below, an image is attached with "
		"numbered red candidate boxes drawn on it. Pick the ONE box number that tightly contains that "
		"field's printed value. If none of the boxes contains it, use null — never guess. Reply ONLY "
		'with JSON: {"selections": [{"field": <key>, "index": <exactly as given>, "mark": <box number or null>}]}. '
		'Copy each selection\'s "field" and "index" EXACTLY as stated in that field\'s line.'
	)
	content = []
	for e in entries:
		roster = "; ".join(f'box {m["n"]}: "{m["text"]}"' for m in e["marks"])
		content.append({"type": "text", "text":
			f'FIELD "{e["field"]}" (reply with "index": {json.dumps(e["index"])})'
			+ f' (label: {e["label"]}): find the printed value {repr(e["raw_text"] or e["value"])[:DETAIL_MAX]}. '
			+ f"Candidates — {roster}. Marked document:"})
		content.append(e["part"])
	return [{"role": "system", "content": system}, {"role": "user", "content": content}]


def build_som_turn(entries):
	"""Set-of-Marks as one session user turn (contract folded into the text)."""
	msgs = build_som_messages(entries)
	return [{"type": "text", "text": msgs[0]["content"]}, *msgs[1]["content"]]


def build_crop_check_messages(entries):
	"""Crop-back verification: for each numbered crop, does it actually show
	the expected value? Models hallucinate boxes rather than abstain — a
	repaired bbox never survives on the repairer's say-so.

	entries: [{"n", "field", "value", "part"}]
	"""
	system = (
		"You are checking small image crops taken from a document. For each numbered crop, read the "
		"text it shows and decide whether the EXPECTED value is printed inside it (ignore case and "
		"spacing differences; the crop may include a little surrounding context). Reply ONLY with JSON: "
		'{"checks": [{"crop": <number>, "contains": true|false, "read_text": <text actually visible>}]}'
	)
	content = []
	for e in entries:
		content.append({"type": "text", "text": f'CROP {e["n"]} — expected value for field "{e["field"]}": {repr(e["value"])[:DETAIL_MAX]}'})
		content.append(e["part"])
	return [{"role": "system", "content": system}, {"role": "user", "content": content}]


def build_crop_check_turn(entries):
	"""Crop-back check as one session user turn (contract folded into text)."""
	msgs = build_crop_check_messages(entries)
	return [{"type": "text", "text": msgs[0]["content"]}, *msgs[1]["content"]]


def build_repair_messages(action, image_parts, fields, deficiencies):
	lines = []
	for d in deficiencies:
		label = _label_for(action, d["field"])
		current = _item_for(fields, d["field"], d.get("index"))
		value_repr = repr(current.get("value") if current else None)[:DETAIL_MAX]
		detail = str(d["detail"])[:DETAIL_MAX]
		if d["kind"] in ("bbox_missing", "bbox_collision", "bbox_insane"):
			lines.append(
				f'- field "{d["field"]}" (label: {label}, index {d.get("index") or 0}): the value {value_repr} was '
				f"extracted WITHOUT a usable bounding box ({detail}). Find that exact printed text near its "
				f'label "{label}" and return its tight bounding box.'
			)
		else:
			lines.append(
				f'- field "{d["field"]}" (label: {label}, index {d.get("index") or 0}): current value {value_repr} is '
				f"wrong or failed a check ({d['kind']}: {detail}). Re-read the correct value from the image."
			)

	system = (
		"You are repairing specific problems in a document extraction. Fix ONLY the listed fields. Reply ONLY with "
		'JSON: {"repairs": [{"field": <key>, "index": <int>, "value": <corrected>, "raw_text": <as printed>, '
		'"confidence": <0..1>, "bbox": [ymin, xmin, ymax, xmax] on the 0..1000 scale}]}. '
		"The bbox MUST tightly enclose the value's printed characters — never the label, never the whole row."
	)
	return [
		{"role": "system", "content": system},
		{"role": "user", "content": [{"type": "text", "text": "PROBLEMS TO FIX:\n" + "\n".join(lines)}, *image_parts]},
	]


def build_repair_turn(action, fields, deficiencies):
	"""Repair as ONE user turn in the executor's session: the document image
	is already in the conversation from the extract turn, so only the repair
	contract + problem list travel — no image re-attach."""
	msgs = build_repair_messages(action, [], fields, deficiencies)
	contract = msgs[0]["content"]
	problems = msgs[1]["content"][0]["text"]
	return [{"type": "text", "text": contract + "\nRe-read from the document image provided earlier in this conversation.\n" + problems}]


def build_match_messages(action, fields, candidates, cfg):
	"""Reconcile the extracted document against fetched candidate records.

	The model sees only the extracted field values and the candidate rows the
	config query returned; it must pick target names from that list. Domain
	rules are the action's `match_guidance` config — no matching logic here.
	"""
	guidance = (cfg.get("match_guidance") or "").strip()
	target = cfg.get("target_doctype")
	system = _join(
		f"You are matching one extracted document against candidate {target} records from an ERP system.",
		"Decide which candidate record(s) the document refers to, using ONLY the evidence shown. "
		"A document may match more than one record, or none.",
		("MATCHING GUIDANCE:\n" + guidance) if guidance else None,
		'Reply ONLY with JSON: {"matches": [{"target": <candidate name>, "confidence": <0..1>, '
		'"reason": <short>}]}. '
		"Use only `name` values from the candidate list — never invent one. "
		"confidence is your calibrated certainty the document refers to that exact record; "
		"return an empty matches array if none is a credible match.",
	)
	payload = {
		"document": _values_only(fields),
		"candidates": candidates,
	}
	return [
		{"role": "system", "content": system},
		{"role": "user", "content": [{"type": "text", "text": json.dumps(payload, default=str, indent=1)}]},
	]


def build_route_messages(action, candidates, want_notes):
	roster = "\n".join(f'- "{c["label"]}": {c["remarks"]}' for c in candidates)
	ask = (
		'Reply ONLY with JSON: {"executor_model": <label>, "verifier_model": <label or null>'
		+ (', "request_notes": <one short paragraph refining the extraction request, or null>' if want_notes else "")
		+ "}."
	)
	system = (
		"You orchestrate an extraction task. Pick which model executes it and which verifies it, "
		"based ONLY on the remarks below. Labels must be copied exactly.\n"
		f"TASK: {action.purpose or action.action_name}\nAVAILABLE MODELS:\n{roster}\n{ask}"
	)
	return [{"role": "system", "content": system}, {"role": "user", "content": "Choose now."}]


# ------------------------------------------------------------ helpers

def _schema_prompt(action):
	schema = action.parsed_schema()
	if not schema:
		return None
	lines = ["OUTPUT FIELDS (return ONLY a JSON object with exactly these keys):"]
	for f in schema:
		kind = "array of objects" if f.get("kind") == "array" else "object"
		lines.append(
			f'- {f["key"]} ({kind}{", required" if f.get("required") else ""}): '
			f'{{"value": <parsed>, "raw_text": <as printed>, "confidence": <0..1>, '
			f'"bbox": [ymin, xmin, ymax, xmax] integers on the 0..1000 scale}}'
			+ (f' — {f["label"]}' if f.get("label") else "")
			+ (f'. {f["hint"]}' if f.get("hint") else "")
		)
	lines.append("Every bbox must tightly enclose the value's printed characters only.")
	return "\n".join(lines)


def _values_only(fields):
	out = {}
	for k, v in fields.items():
		if isinstance(v, list):
			out[k] = [{"value": i["value"], "raw_text": i["raw_text"]} for i in v]
		elif v:
			out[k] = {"value": v["value"], "raw_text": v["raw_text"]}
		else:
			out[k] = None
	return out


def _iter_items(fields, schema_field):
	entry = fields.get(schema_field["key"])
	if isinstance(entry, list):
		for idx, item in enumerate(entry):
			yield idx, item
	else:
		yield None, entry


def _item_for(fields, key, index):
	entry = fields.get(key)
	if isinstance(entry, list):
		if index is not None and 0 <= index < len(entry):
			return entry[index]
		return entry[0] if entry else None
	return entry


def _label_for(action, key):
	for f in action.parsed_schema():
		if f["key"] == key:
			return f.get("label") or key
	return key


def _deficiency(schema_field, index, kind, detail):
	return {"field": schema_field["key"], "index": index, "kind": kind, "detail": detail}


def _join(*parts):
	return "\n\n".join(p for p in parts if p)

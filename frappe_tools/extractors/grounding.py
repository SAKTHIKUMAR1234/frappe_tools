"""Replace approximate model boxes with locally OCR-grounded source regions.

The vision model remains responsible for semantic extraction. Tesseract is used
only to locate the already-extracted printed text on the original page. This is
free, local and auditable; model coordinates remain as an explicitly marked
fallback when the printed text cannot be matched confidently.
"""

import json
import math

import frappe
from frappe.utils import cint

from frappe_tools.i2a import ground


def refresh(extraction_name):
	"""Maintenance entry point for grounding an already processed extraction."""
	doc = frappe.get_doc("Document Extraction", extraction_name)
	stats = apply(doc)
	doc.save(ignore_permissions=True)
	return stats


def apply(doc):
	"""Ground all field and row evidence in-place. Returns compact run stats."""
	pages = _load_pages(doc)
	if not pages:
		fallback = _tag_model_fallbacks(doc)
		return {"available": False, "grounded": 0, "fallback": fallback}

	stats = {"available": True, "grounded": 0, "fallback": 0}
	for row in doc.extracted_fields:
		current = _as_box(row.bbox_json)
		if current.get("source") == "manual":
			continue
		match = locate(
			[row.llm_raw_text, row.llm_value, row.value],
			pages,
			cint(row.source_page),
			_model_box(current),
		)
		resolved = _resolved_box(match, current, cint(row.source_page))
		row.bbox_json = json.dumps(resolved, ensure_ascii=False) if resolved else None
		if match:
			row.source_page = match["page"]
			stats["grounded"] += 1
		elif current:
			row.source_page = cint(resolved.get("model_page")) or row.source_page
			stats["fallback"] += 1

	for row in doc.lines:
		current = _as_box(row.bbox_json)
		data = _json_object(row.raw_json)
		cell_evidence = data.get("_cell_evidence") if isinstance(data.get("_cell_evidence"), dict) else {}
		for key, evidence in cell_evidence.items():
			if not isinstance(evidence, dict) or evidence.get("source") == "manual":
				continue
			claimed = _as_box(evidence.get("bbox"))
			cell_match = locate([evidence.get("raw_text"), data.get(key)], pages,
				cint(evidence.get("page")) or cint(row.source_page), _model_box(claimed))
			resolved_cell = _resolved_box(cell_match, claimed,
				cint(evidence.get("page")) or cint(row.source_page))
			base_evidence = {name: evidence.get(name) for name in ("raw_text", "confidence", "page", "bbox")}
			cell_evidence[key] = {**base_evidence, **resolved_cell}
			if cell_match:
				cell_evidence[key]["page"] = cell_match["page"]
				stats["grounded"] += 1
			elif claimed:
				stats["fallback"] += 1
		if cell_evidence:
			data["_cell_evidence"] = cell_evidence
			row.raw_json = json.dumps(data, ensure_ascii=False)
		if current.get("source") == "manual":
			continue
		targets = [
			row.description,
			row.supplier_code,
			row.hsn,
			*[value for value in data.values() if isinstance(value, (str, int, float))],
		]
		match = locate_row(targets, pages, cint(row.source_page), _model_box(current))
		resolved = _resolved_box(match, current, cint(row.source_page))
		row.bbox_json = json.dumps(resolved, ensure_ascii=False) if resolved else None
		if match:
			row.source_page = match["page"]
			stats["grounded"] += 1
		elif current:
			row.source_page = cint(resolved.get("model_page")) or row.source_page
			stats["fallback"] += 1
	return stats


def _tag_model_fallbacks(doc):
	count = 0
	for row in [*doc.extracted_fields, *doc.lines]:
		current = _as_box(row.bbox_json)
		if not current or current.get("source"):
			continue
		resolved = _resolved_box(None, current, cint(row.source_page))
		row.bbox_json = json.dumps(resolved, ensure_ascii=False)
		if resolved:
			count += 1
	return count


def locate(targets, pages, claimed_page=0, claimed_bbox=None):
	"""Choose a text match, using the model box only to disambiguate duplicates."""
	candidates = []
	for page_no, context in pages.items():
		for candidate in ground.match_value(_unique_targets(targets), context["words"]):
			box = _usable_box(candidate.get("bbox"), context.get("width"), context.get("height"))
			if not box:
				continue
			candidate = {**candidate, "bbox": box}
			candidate = {**candidate, "page": page_no}
			candidate["rank"] = _rank(candidate, claimed_page, claimed_bbox)
			candidates.append(candidate)
	if not candidates:
		return None
	if claimed_page:
		# Page selection from the vision pass is substantially more reliable
		# than fuzzy OCR text on another page. Never move evidence across pages.
		candidates = [item for item in candidates if item["page"] == claimed_page]
		if not candidates:
			return None

	candidates.sort(key=lambda item: (-item["rank"], -item["score"]))
	best = candidates[0]
	if best["score"] < ground.MATCH_FLOOR:
		return None
	if claimed_bbox:
		distance = _center_distance(best["bbox"], claimed_bbox)
		short_value = max((len(ground._norm(value)) for value in _unique_targets(targets)), default=0) <= 4
		if distance > (0.15 if short_value else 0.30):
			return None

	# An approximate model region is useful only as a tie-breaker; it never
	# becomes the returned box. Without it, require a clearly unique match.
	if not claimed_bbox:
		rivals = [
			item for item in candidates[1:]
			if not _same_match(best, item)
		]
		if rivals and best["rank"] - rivals[0]["rank"] < 0.08:
			return None
	return {key: best[key] for key in ("bbox", "text", "score", "page")}


def _center_distance(first, second):
	return math.dist(
		(first["x"] + first["w"] / 2, first["y"] + first["h"] / 2),
		(second["x"] + second["w"] / 2, second["y"] + second["h"] / 2),
	)


def _same_match(first, second):
	if ground._same_spot(first["bbox"], second["bbox"]):
		return True
	if ground._norm(first.get("text")) != ground._norm(second.get("text")):
		return False
	a, b = first["bbox"], second["bbox"]
	return math.dist(
		(a["x"] + a["w"] / 2, a["y"] + a["h"] / 2),
		(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2),
	) < 0.12


def locate_row(targets, pages, claimed_page=0, claimed_bbox=None):
	"""Ground a child row by combining independently located scalar values."""
	matches = []
	for target in _unique_targets(targets):
		match = locate([target], pages, claimed_page, claimed_bbox)
		if match:
			matches.append(match)
	if not matches:
		return None
	page = claimed_page or matches[0]["page"]
	matches = [match for match in matches if match["page"] == page]
	if not matches:
		return None
	return {
		"bbox": ground.union_bbox([match["bbox"] for match in matches]),
		"text": " | ".join(dict.fromkeys(match["text"] for match in matches))[:250],
		"score": sum(float(match["score"]) for match in matches) / len(matches),
		"page": page,
	}


def _load_pages(doc):
	pages = {}
	for page in doc.pages:
		if not page.image:
			continue
		try:
			pil = ground.load_image(page.image)
			words = ground.ocr_word_boxes(pil)
		except Exception:
			continue
		if words is None:
			return {}
		pages[cint(page.page_no)] = {"words": words, "width": pil.width, "height": pil.height}
	return pages


def _rank(candidate, claimed_page, claimed_bbox):
	rank = float(candidate["score"])
	if claimed_page and candidate["page"] == claimed_page:
		rank += 0.12
	if claimed_bbox and candidate["page"] == claimed_page:
		a = candidate["bbox"]
		distance = math.dist(
			(a["x"] + a["w"] / 2, a["y"] + a["h"] / 2),
			(claimed_bbox["x"] + claimed_bbox["w"] / 2, claimed_bbox["y"] + claimed_bbox["h"] / 2),
		)
		rank += max(0.0, 0.3 * (1.0 - min(distance, 1.0)))
	return rank


def _resolved_box(match, current, current_page=0):
	model = _model_box(current)
	model_page = cint(current.get("model_page")) or current_page
	if not match:
		return {
			**model, "source": "model", "verified": False,
			"model_bbox": model, "model_page": model_page,
		} if model else {}
	box = dict(match["bbox"])
	box.update({
		"source": "ocr",
		"verified": True,
		"score": round(float(match["score"]), 4),
		"matched_text": str(match["text"])[:250],
	})
	if model:
		box["model_bbox"] = model
		box["model_page"] = model_page
	return box


def _model_box(box):
	if isinstance(box.get("model_bbox"), dict):
		return _usable_box(box["model_bbox"])
	if all(key in box for key in ("x", "y", "w", "h")):
		return _usable_box(box)
	return None


def _usable_box(value, width=0, height=0):
	if not isinstance(value, dict) or not all(key in value for key in ("x", "y", "w", "h")):
		return None
	try:
		box = {key: float(value[key]) for key in ("x", "y", "w", "h")}
	except (TypeError, ValueError):
		return None
	if not all(math.isfinite(number) for number in box.values()):
		return None
	if box["x"] < 0 or box["y"] < 0 or box["w"] < 0.002 or box["h"] < 0.002:
		return None
	if box["x"] + box["w"] > 1.0001 or box["y"] + box["h"] > 1.0001:
		return None
	if width and box["w"] * width < 3:
		return None
	if height and box["h"] * height < 3:
		return None
	return {key: round(number, 4) for key, number in box.items()}


def _as_box(value):
	if isinstance(value, dict):
		return value
	try:
		parsed = json.loads(value or "{}")
		return parsed if isinstance(parsed, dict) else {}
	except (TypeError, ValueError):
		return {}


def _json_object(value):
	try:
		parsed = json.loads(value or "{}") if not isinstance(value, dict) else value
		return parsed if isinstance(parsed, dict) else {}
	except (TypeError, ValueError):
		return {}


def _unique_targets(values):
	result = []
	for value in values:
		text = str(value or "").strip()
		if text and text not in result:
			result.append(text)
	return result

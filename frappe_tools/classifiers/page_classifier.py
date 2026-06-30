# Copyright (c) 2026, contributors
# For license information, please see license.txt

"""AI page classifier — a shared frappe_tools capability.

Given a set of scanned page images + a list of allowed section labels (the
section titles of a Document Scanner Layout), it asks the vision model to pick
exactly one label per page (or 'unknown') and whether each page is Front/Back.

CLASSIFICATION ONLY — it never extracts field/line values. Built on
frappe_tools.utils.llm.call_vision (OpenRouter/Gemini, JSON mode).
"""

import json
import difflib

from frappe_tools.utils import llm

SYSTEM_PROMPT = (
	"You are a document page CLASSIFIER for scanned business documents. You are given "
	"one or more page images IN ORDER and a fixed list of allowed SECTION labels. For each "
	"page, choose exactly ONE label from the allowed list, or 'unknown' if no label fits. "
	"Also decide whether the page is the FRONT (primary/first side) or BACK (continuation/"
	"reverse) of its document. You CLASSIFY ONLY — never transcribe or extract field values. "
	"Respond with ONE valid JSON object and nothing else."
)


def build_user_prompt(labels, n_pages):
	return "\n".join([
		"ALLOWED SECTION LABELS (choose exactly one per page, verbatim):",
		json.dumps(labels, ensure_ascii=False, indent=2),
		'If none fit, use the literal string "unknown".',
		f"\nThere are {n_pages} page image(s), in order; page index is 1-based.",
		"\nRULES:",
		"- 'section' MUST be one of the allowed labels or 'unknown'.",
		"- 'page_type' is 'Front' or 'Back' (Back = continuation / reverse / annexure of the previous page).",
		"- 'confidence' is 0.0-1.0.",
		"- Pages belonging to the same physical document keep the same label.",
		"\nOUTPUT (exactly this shape):",
		'{ "pages": [ {"page": 1, "section": "<label-or-unknown>", "page_type": "Front", "confidence": 0.0} ] }',
	])


def _coerce(raw_pages, labels, n_pages):
	"""Pure, defensive normaliser — guarantees exactly one clean row per input page.

	Snaps each section to the allowed label set (exact -> case-insensitive ->
	fuzzy 0.8 -> 'unknown') so a downstream Link constraint can never fail.
	"""
	label_by_lower = {l.lower(): l for l in labels}
	by_page = {}
	for r in (raw_pages or []):
		try:
			p = int(r.get("page"))
		except (TypeError, ValueError, AttributeError):
			continue
		if p < 1 or p > n_pages or p in by_page:
			continue
		sec = (r.get("section") or "").strip()
		low = sec.lower()
		if low in label_by_lower:
			sec = label_by_lower[low]
		elif sec and low != "unknown":
			match = difflib.get_close_matches(sec, labels, n=1, cutoff=0.8)
			sec = match[0] if match else "unknown"
		else:
			sec = "unknown"
		pt = (r.get("page_type") or "Front").strip().title()
		if pt not in ("Front", "Back"):
			pt = "Front"
		try:
			conf = max(0.0, min(1.0, float(r.get("confidence"))))
		except (TypeError, ValueError):
			conf = 0.0
		by_page[p] = {"page": p, "section": sec, "page_type": pt, "confidence": conf}

	return [
		by_page.get(p, {"page": p, "section": "unknown", "page_type": "Front", "confidence": 0.0})
		for p in range(1, n_pages + 1)
	]


def classify(labels, image_data_urls):
	"""labels: allowed section titles. image_data_urls: ordered data: URLs.

	Returns {model, latency_ms, usage, pages:[{page, section, page_type, confidence}]}.
	"""
	n = len(image_data_urls)
	result = llm.call_vision(image_data_urls, SYSTEM_PROMPT, build_user_prompt(labels, n))
	raw = (result.get("data") or {}).get("pages") or []
	return {
		"model": result.get("model"),
		"latency_ms": result.get("latency_ms"),
		"usage": result.get("usage"),
		"pages": _coerce(raw, labels, n),
	}

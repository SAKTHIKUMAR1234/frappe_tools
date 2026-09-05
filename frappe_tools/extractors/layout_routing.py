"""Select a configured scanner layout and put uploaded pages in its logical order.

The legacy scanner already models document packages through ``Document Scanner
Layout`` and its ordered sections.  The user selects that layout at intake; the
automatic runtime classifies every page into one of its sections and puts the
package into logical order before field extraction.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt

from frappe_tools.utils import llm


MIN_LAYOUT_CONFIDENCE = 0.85
MIN_PAGE_CONFIDENCE = 0.75
OTHERS_SECTION = "Others"

SYSTEM_PROMPT = (
	"You route and order pages in a scanned Frappe business-document package. "
	"The user has already selected the supplied layout. Classify every input page into exactly one "
	"section belonging to that layout, identify front/back or continuation pages, "
	"and give their logical order. Never extract business field values. Do not invent "
	"a layout or section. Return exactly one JSON object."
)


def candidate_layouts(target_doctype):
	"""Return configured layouts and their authoritative section order."""
	rows = frappe.get_all(
		"Document Scanner Layout",
		filters={"layout_doctype": target_doctype},
		fields=["name", "layout_doctype"],
		order_by="name asc",
		limit_page_length=500,
	)
	if not rows:
		return []
	sections = frappe.get_all(
		"Document Scanner Layout Section",
		filters={"parent": ["in", [row.name for row in rows]], "parenttype": "Document Scanner Layout"},
		fields=["parent", "idx", "title", "layout_type"],
		order_by="parent asc, idx asc",
		limit_page_length=2000,
	)
	by_layout = {}
	for section in sections:
		by_layout.setdefault(section.parent, []).append({
			"title": section.title,
			"layout_type": section.layout_type,
			"order": cint(section.idx),
		})
	profiles = []
	for row in rows:
		layout_sections = by_layout.get(row.name, [])
		if not any(section["title"].casefold() == OTHERS_SECTION.casefold() for section in layout_sections):
			layout_sections.append({
				"title": OTHERS_SECTION,
				"layout_type": "Series Vertical",
				"order": max((section["order"] for section in layout_sections), default=0) + 1,
			})
		profiles.append({"layout": row.name, "target_doctype": row.layout_doctype, "sections": layout_sections})
	return profiles


def prepare(doc):
	"""Categorize/order pages inside the layout explicitly selected by the user.

	Returns ``ready=False`` for a safe layout handoff.  No target document or
	external record is ever changed here.
	"""
	profiles = candidate_layouts(doc.target_doctype)
	if not profiles:
		doc.layout_phase = "Not Required"
		return {"ready": True, "required": False}
	if doc.get("layout_phase") == "Ordered" and doc.get("selected_layout"):
		return {"ready": True, "required": True, "layout": doc.selected_layout, "reused": True}

	selected = doc.get("selected_layout") or _preselected_layout(doc, profiles)
	if not selected:
		# Layout choice is an intake decision.  Spending a vision call to guess it
		# makes the categorised archive unstable and contradicts the scanner UX.
		return _handoff(doc, ["select_a_document_scanner_layout_before_processing"])
	profiles = [profile for profile in profiles if profile["layout"] == selected]
	if not profiles:
		return _handoff(doc, ["selected_layout_does_not_match_target_doctype"])

	ordered_input = sorted((page for page in doc.pages if page.image), key=lambda page: cint(page.page_no))
	from frappe_tools.extractors.pipeline import file_to_data_url

	images = [file_to_data_url(page.image) for page in ordered_input]
	result = llm.call_vision(
		images,
		SYSTEM_PROMPT,
		_build_prompt(profiles, len(images), selected, doc.get("classification_json")),
		extraction=doc.name,
		target_doctype=doc.target_doctype,
	)
	decision = _normalize_decision(result.get("data"), profiles, len(images), required_layout=selected)
	doc.layout_model = result.get("model")
	doc.layout_confidence = decision["confidence"] * 100
	doc.layout_json = json.dumps(decision, ensure_ascii=False)
	usage = result.get("usage") or {}
	doc.total_tokens = cint(doc.total_tokens) + cint(usage.get("total_tokens"))
	doc.cost_usd = flt(doc.cost_usd) + flt(usage.get("cost"))
	if not decision["accepted"]:
		return _handoff(doc, decision["handoff_reasons"], decision)

	_apply_page_plan(doc, decision, profiles[0])
	_sync_existing_scan_details(doc)
	doc.selected_layout = decision["layout"]
	doc.layout_phase = "Ordered"
	doc.handoff_reason = None
	doc.add_processing_event(
		"Layout Routing",
		"Ordered",
		f"Categorized and ordered {len(decision['pages'])} page(s) inside user-selected layout {decision['layout']}.",
		decision,
	)
	doc.flags.skip_auto_process = True
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ready": True, "required": True, "layout": decision["layout"], "decision": decision}


def accept_layout(extraction_name, layout):
	"""Human override for an uncertain layout; page classification still runs."""
	doc = frappe.get_doc("Document Extraction", extraction_name)
	doc.check_permission("write")
	if doc.status != "Layout Handoff":
		frappe.throw(_("This extraction is not waiting for a layout decision."))
	allowed = {profile["layout"] for profile in candidate_layouts(doc.target_doctype)}
	if layout not in allowed:
		frappe.throw(_("Layout {0} is not configured for {1}.").format(layout, doc.target_doctype))
	doc.selected_layout = layout
	doc.layout_phase = "Human Confirmed"
	doc.status = "Queued"
	doc.handoff_reason = None
	doc.error_log = None
	doc.add_processing_event("Layout Routing", "Human Confirmed", f"{frappe.session.user} selected {layout}.")
	doc.flags.skip_auto_process = True
	doc.save()
	frappe.db.commit()
	from frappe_tools.extractors.pipeline import enqueue_extraction

	job = enqueue_extraction(doc.name, len(doc.pages), enqueue_after_commit=False)
	frappe.db.set_value("Document Extraction", doc.name, "processing_job_id", job["job_id"], update_modified=False)
	frappe.db.commit()
	return {"extraction": doc.name, "status": "Queued", **job}


def _build_prompt(profiles, page_count, selected=None, classification_json=None):
	hint = ""
	try:
		classification = json.loads(classification_json or "{}")
		if classification.get("document_category"):
			hint = f"\nEarlier document-family hint (not authoritative): {classification['document_category']}"
	except (TypeError, ValueError):
		pass
	selection = f"The user selected layout {selected!r}; use exactly this layout and never replace it."
	return (
		f"There are {page_count} input page image(s), numbered 1..{page_count} in upload order. {selection}{hint}\n"
		"Configured layouts (section order is authoritative):\n"
		+ json.dumps(profiles, ensure_ascii=False, indent=2)
		+ "\nReturn: {\"layout\": <exact supplied layout>, \"confidence\": <0..1>, "
		"\"reasons\": [<brief visual reasons>], \"handoff_reasons\": [<ambiguities>], "
		"\"pages\": [{\"input_page\": <1-based>, \"section\": <exact section>, "
		"\"page_type\": \"Front\"|\"Back\", \"document_sequence\": <1-based document within section>, "
		"\"page_sequence\": <1-based page within that document>, \"confidence\": <0..1>}]}. "
		"Return every input page exactly once. Page sequence must reflect the document's logical reading order even when uploads are shuffled. "
		"When a page does not clearly belong to a specific section, assign it to the exact 'Others' section; never omit it."
	)


def _normalize_decision(value, profiles, page_count, required_layout=None):
	value = value if isinstance(value, dict) else {}
	profile = _profile(profiles, required_layout or value.get("layout"))
	confidence = _bounded(value.get("confidence"))
	handoff = [str(item)[:300] for item in value.get("handoff_reasons") or []]
	if not profile:
		handoff.append("unsupported_or_unresolved_layout")
	if not required_layout and confidence < MIN_LAYOUT_CONFIDENCE:
		handoff.append(f"layout_confidence_below_{MIN_LAYOUT_CONFIDENCE:.2f}")
	sections = {section["title"]: section for section in (profile or {}).get("sections", [])}
	sections_lower = {name.casefold(): name for name in sections}
	others = sections_lower.get(OTHERS_SECTION.casefold())
	pages_by_input = {}
	for raw in value.get("pages") or []:
		if not isinstance(raw, dict):
			continue
		input_page = cint(raw.get("input_page"))
		if not 1 <= input_page <= page_count or input_page in pages_by_input:
			continue
		section = str(raw.get("section") or "").strip()
		section = sections_lower.get(section.casefold()) or others
		page_confidence = _bounded(raw.get("confidence"))
		page_type = str(raw.get("page_type") or "Front").strip().title()
		if page_type not in {"Front", "Back"}:
			page_type = "Front"
		pages_by_input[input_page] = {
			"input_page": input_page,
			"section": section,
			"layout_type": sections.get(section, {}).get("layout_type") if section else None,
			"page_type": page_type,
			"document_sequence": max(cint(raw.get("document_sequence")), 1),
			"page_sequence": max(cint(raw.get("page_sequence")), 1),
			"confidence": page_confidence,
			"fallback": bool(section == others and str(raw.get("section") or "").strip().casefold() != OTHERS_SECTION.casefold()),
		}
	for input_page in range(1, page_count + 1):
		page = pages_by_input.get(input_page)
		if not page:
			if others:
				pages_by_input[input_page] = {
					"input_page": input_page,
					"section": others,
					"layout_type": sections[others]["layout_type"],
					"page_type": "Front",
					"document_sequence": input_page,
					"page_sequence": 1,
					"confidence": 0,
					"fallback": True,
				}
			else:
				handoff.append(f"page_{input_page}_section_unresolved")
		elif page["confidence"] < MIN_PAGE_CONFIDENCE and others:
			page["section"] = others
			page["layout_type"] = sections[others]["layout_type"]
			page["fallback"] = True
		elif page["confidence"] < MIN_PAGE_CONFIDENCE:
			handoff.append(f"page_{input_page}_confidence_below_{MIN_PAGE_CONFIDENCE:.2f}")
	return {
		"layout": profile["layout"] if profile else None,
		"confidence": confidence,
		"reasons": [str(item)[:300] for item in value.get("reasons") or []],
		"handoff_reasons": list(dict.fromkeys(handoff)),
		"pages": [pages_by_input[key] for key in sorted(pages_by_input)],
		"accepted": bool(profile and (required_layout or confidence >= MIN_LAYOUT_CONFIDENCE) and not handoff),
	}


def _apply_page_plan(doc, decision, profile):
	section_order = {section["title"]: cint(section["order"]) for section in profile["sections"]}
	plan = {page["input_page"]: page for page in decision["pages"]}
	input_rows = sorted((page for page in doc.pages if page.image), key=lambda page: cint(page.page_no))
	for input_page, row in enumerate(input_rows, 1):
		page = plan[input_page]
		row.original_page_no = cint(row.get("original_page_no")) or cint(row.page_no) or input_page
		row.layout_section = page["section"]
		row.layout_type = page["layout_type"]
		row.page_type = page["page_type"]
		row.document_sequence = page["document_sequence"]
		row.page_sequence = page["page_sequence"]
		row.layout_confidence = page["confidence"] * 100
	ordered = sorted(input_rows, key=lambda row: (
		section_order.get(row.layout_section, 9999),
		cint(row.document_sequence) or 1,
		cint(row.page_sequence) or (2 if row.page_type == "Back" else 1),
		cint(row.original_page_no),
	))
	doc.set("pages", ordered)
	for page_no, row in enumerate(doc.pages, 1):
		row.idx = page_no
		row.page_no = page_no


def _sync_existing_scan_details(doc):
	"""Persist AI categorisation on existing scan rows without touching file bytes.

	Backfilled scans already point at ``Scanned Document Detail`` rows.  Updating
	their metadata makes the legacy layout viewer immediately browseable while
	the attachment itself remains unchanged (and therefore cannot trigger an S3
	delete or upload).
	"""
	for page in doc.pages:
		if not page.get("scanned_document_detail"):
			continue
		frappe.db.set_value(
			"Scanned Document Detail",
			page.scanned_document_detail,
			{
				"page_no": cint(page.page_no),
				"title": page.layout_section,
				"layout_type": page.layout_type,
				"page_type": page.page_type,
				"is_deleted": 0,
			},
			update_modified=False,
		)


def _preselected_layout(doc, profiles):
	allowed = {profile["layout"] for profile in profiles}
	if doc.get("scanned_document"):
		layout = frappe.db.get_value("Scanned Document", doc.scanned_document, "scanner_layout")
		return layout if layout in allowed else None
	details = [page.scanned_document_detail for page in doc.pages if page.get("scanned_document_detail")]
	if not details:
		return None
	parents = set(frappe.get_all("Scanned Document Detail", filters={"name": ["in", details]}, pluck="scanner_document"))
	parents.discard(None)
	if len(parents) != 1:
		return None
	layout = frappe.db.get_value("Scanned Document", next(iter(parents)), "scanner_layout")
	return layout if layout in allowed else None


def _handoff(doc, reasons, decision=None):
	doc.status = "Layout Handoff"
	doc.layout_phase = "Human Handoff"
	doc.handoff_reason = "; ".join(list(dict.fromkeys(reasons)))
	doc.add_processing_event(
		"Layout Routing", "Human Handoff", "The document layout or page order needs confirmation.", decision
	)
	doc.flags.skip_auto_process = True
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ready": False, "required": True, "status": doc.status, "handoff_reasons": reasons}


def _profile(profiles, layout):
	return next((profile for profile in profiles if profile["layout"] == layout), None)


def _bounded(value):
	return max(0.0, min(flt(value), 1.0))

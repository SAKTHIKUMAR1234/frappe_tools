# Copyright (c) 2026, contributors
# For license information, please see license.txt

"""Whitelisted API for AI page-classification (shared frappe_tools capability)."""

import frappe
from frappe import _
from frappe_tools.classifiers import page_classifier
from frappe_tools.extractors.pipeline import file_to_data_url
from frappe_tools.file_data.documents import PAGE_ORDER, active_page_filters


def _layout_sections(layout_name):
	layout = frappe.get_doc("Document Scanner Layout", layout_name)
	return [{"title": s.title, "layout_type": s.layout_type} for s in layout.layout_doctype_sections]


def load_classification_pages(scanned_document: str) -> tuple[list, list[str]]:
	"""Share ordered active pages and the existing S3-aware image reader."""
	details = frappe.get_all(
		"Scanned Document Detail",
		filters=active_page_filters(scanned_document),
		fields=[
			"name", "attachment", "original_attachment", "page_no", "page_type", "title",
			"layout_type", "is_deleted", "scanner_document",
		],
		order_by=PAGE_ORDER,
	)
	return details, [file_to_data_url(d.attachment) for d in details]


@frappe.whitelist()
def get_layout_labels(layout):
	"""The classification vocabulary for a layout (also drives any UI)."""
	sections = _layout_sections(layout)
	return {
		"layout": layout,
		"sections": sections,
		"labels": [s["title"] for s in sections],
	}


@frappe.whitelist(methods=["POST"])
def classify_scanned_document(scanned_document: str) -> dict:
	"""Classify every page of a Scanned Document into its layout's sections,
	writing title / page_type / layout_type back onto each Scanned Document Detail.
	"""
	from frappe_tools.scan_categorization.categorize import check_scan_permission, retire_taken_slots
	from frappe_tools.scan_categorization.page_data import lock_page_snapshot

	sd = frappe.get_doc("Scanned Document", scanned_document)
	check_scan_permission(sd)
	if sd.ai_reviewed:
		frappe.throw(_("This scan has been reviewed and is protected from AI changes."))
	if not sd.scanner_layout:
		frappe.throw(_("This scan has no layout assigned."))

	sections = _layout_sections(sd.scanner_layout)
	if not sections:
		frappe.throw(_("The layout '{0}' has no sections.").format(sd.scanner_layout))

	lt_by_label = {s["title"]: s["layout_type"] for s in sections}

	details, image_urls = load_classification_pages(scanned_document)
	if not details:
		frappe.throw(_("No pages to classify."))

	res = page_classifier.classify(list(lt_by_label), image_urls)
	pages = [_classified_page(res["pages"], idx, lt_by_label) for idx in range(len(details))]
	values = [
		{"title": p["section"], "page_no": d.page_no, "page_type": p["page_type"], "layout_type": lt_by_label[p["section"]]}
		for d, p in zip(details, pages, strict=True)
	]

	_current, reason = lock_page_snapshot(sd, details)
	if reason:
		frappe.throw(reason)
	retire_taken_slots(sd.name, values)
	for d, value in zip(details, values, strict=True):
		frappe.db.set_value("Scanned Document Detail", d.name, value, update_modified=False)

	updated = [
		{"detail": d.name, "page_no": d.page_no, "section": p["section"], "page_type": p["page_type"],
			"confidence": p["confidence"]}
		for d, p in zip(details, pages, strict=True)
	]
	return {"model": res.get("model"), "pages": updated}


def _classified_page(pages: list[dict], idx: int, lt_by_label: dict) -> dict:
	"""Return page `idx` of the result; a missing page or a label outside the layout goes to the first section."""
	page = pages[idx] if idx < len(pages) else {"section": "unknown", "page_type": "Front", "confidence": 0.0}
	section = page.get("section")
	return {
		"section": section if section in lt_by_label else next(iter(lt_by_label)),
		"page_type": page.get("page_type") or "Front",
		"confidence": page.get("confidence"),
	}

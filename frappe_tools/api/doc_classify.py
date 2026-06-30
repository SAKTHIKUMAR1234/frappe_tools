# Copyright (c) 2026, contributors
# For license information, please see license.txt

"""Whitelisted API for AI page-classification (shared frappe_tools capability)."""

import frappe
from frappe import _

from frappe_tools.classifiers import page_classifier
from frappe_tools.extractors.pipeline import file_to_data_url


def _layout_sections(layout_name):
	layout = frappe.get_doc("Document Scanner Layout", layout_name)
	return [{"title": s.title, "layout_type": s.layout_type} for s in layout.layout_doctype_sections]


@frappe.whitelist()
def get_layout_labels(layout):
	"""The classification vocabulary for a layout (also drives any UI)."""
	sections = _layout_sections(layout)
	return {
		"layout": layout,
		"sections": sections,
		"labels": [s["title"] for s in sections],
	}


@frappe.whitelist()
def classify_scanned_document(scanned_document):
	"""Classify every page of a Scanned Document into its layout's sections,
	writing title / page_type / layout_type back onto each Scanned Document Detail.
	"""
	sd = frappe.get_doc("Scanned Document", scanned_document)
	if not sd.scanner_layout:
		frappe.throw(_("This scan has no layout assigned."))

	sections = _layout_sections(sd.scanner_layout)
	if not sections:
		frappe.throw(_("The layout '{0}' has no sections.").format(sd.scanner_layout))

	labels = [s["title"] for s in sections]
	lt_by_label = {s["title"]: s["layout_type"] for s in sections}
	default_lt = sections[0]["layout_type"] or "Single Page"

	details = frappe.get_all(
		"Scanned Document Detail",
		filters={"scanner_document": scanned_document, "is_deleted": 0},
		fields=["name", "attachment", "page_no"],
		order_by="page_no asc",
	)
	if not details:
		frappe.throw(_("No pages to classify."))

	image_urls = [file_to_data_url(d["attachment"]) for d in details]
	res = page_classifier.classify(labels, image_urls)
	pages = res["pages"]

	updated = []
	for idx, d in enumerate(details):
		p = pages[idx] if idx < len(pages) else {"section": "unknown", "page_type": "Front", "confidence": 0.0}
		section = p.get("section") or "unknown"
		# 'unknown' (or any non-layout label) -> snap to a real section so the reqd Link stays valid
		if section not in lt_by_label:
			section = labels[0]
		frappe.db.set_value(
			"Scanned Document Detail",
			d["name"],
			{
				"title": section,
				"page_type": p.get("page_type") or "Front",
				"layout_type": lt_by_label.get(section, default_lt),
			},
			update_modified=False,
		)
		updated.append({
			"detail": d["name"],
			"page_no": d["page_no"],
			"section": section,
			"page_type": p.get("page_type"),
			"confidence": p.get("confidence"),
		})

	return {"model": res.get("model"), "pages": updated}

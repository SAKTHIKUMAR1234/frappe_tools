import frappe

from frappe_tools.frappe_tools.doctype.document_scanner_layout.document_scanner_layout import (
	OTHERS_SECTION,
	ensure_others_section_title,
)


def execute():
	"""Backfill the lossless Others bucket on existing local scanner layouts."""
	ensure_others_section_title()
	for name in frappe.get_all("Document Scanner Layout", pluck="name"):
		doc = frappe.get_doc("Document Scanner Layout", name)
		if any(str(row.title or "").strip().casefold() == OTHERS_SECTION.casefold()
			for row in doc.layout_doctype_sections):
			continue
		doc.append("layout_doctype_sections", {
			"title": OTHERS_SECTION,
			"layout_type": "Series Vertical",
		})
		doc.flags.ignore_validate_update_after_submit = True
		doc.save(ignore_permissions=True)

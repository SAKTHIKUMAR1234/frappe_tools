# Copyright (c) 2025, sakthi123msd@gmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


OTHERS_SECTION = "Others"


class DocumentScannerLayout(Document):
	def before_validate(self):
		"""Every package needs a lossless bucket for unrecognised pages."""
		ensure_others_section_title()
		if not any(str(row.title or "").strip().casefold() == OTHERS_SECTION.casefold()
			for row in self.layout_doctype_sections):
			self.append("layout_doctype_sections", {
				"title": OTHERS_SECTION,
				"layout_type": "Series Vertical",
			})


def ensure_others_section_title():
	if frappe.db.exists("Document Layout Section Title", OTHERS_SECTION):
		return
	doc = frappe.new_doc("Document Layout Section Title")
	doc.title = OTHERS_SECTION
	doc.insert(ignore_permissions=True)

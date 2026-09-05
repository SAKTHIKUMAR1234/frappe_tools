"""Backfill the record type used by Document Extraction Line's Dynamic Link."""

import frappe


def execute():
	if not frappe.db.has_column("Document Extraction Line", "matched_doctype"):
		return
	frappe.db.sql(
		"""
		UPDATE `tabDocument Extraction Line` line
		INNER JOIN `tabDocument Extraction` extraction ON extraction.name = line.parent
		SET line.matched_doctype = CASE
			WHEN extraction.target_doctype = 'Purchase Invoice' THEN 'Item'
			WHEN extraction.target_doctype IN ('LR Processing', 'LR Processing Entry', 'LR Processing Batch') THEN 'Sales Invoice'
			ELSE line.matched_doctype
		END
		WHERE line.matched_item IS NOT NULL AND line.matched_item != ''
			AND (line.matched_doctype IS NULL OR line.matched_doctype = '')
		"""
	)

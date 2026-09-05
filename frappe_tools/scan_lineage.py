"""Keep immutable scan evidence attached across create/cancel/amend versions."""

import frappe
from frappe.utils import cint, now_datetime

INTERNAL = {"Scanned Document", "Scanned Document Detail", "Scanned Document Reference"}


def create_reference(scanned_document, target_doctype, target_name, amended_from_reference=None):
	if not scanned_document or not target_doctype or not target_name:
		return None
	existing = frappe.db.exists("Scanned Document Reference", {"scanned_document": scanned_document,
		"target_doctype": target_doctype, "target_name": target_name})
	if existing:
		return existing
	doc = frappe.get_doc({"doctype": "Scanned Document Reference", "scanned_document": scanned_document,
		"target_doctype": target_doctype, "target_name": target_name, "status": "Active", "active": 1,
		"amended_from_reference": amended_from_reference})
	doc.insert(ignore_permissions=True)
	return doc.name


def link_extraction_to_target(extraction, target_name):
	"""Materialize one extraction's categorized pages as immutable scan lineage.

	Existing ``Scanned Document Detail`` rows are reused. New capture pages gain
	one detail row pointing at the existing local File URL; file bytes are never
	copied, moved, uploaded, or deleted here.
	"""
	if isinstance(extraction, str):
		extraction = frappe.get_doc("Document Extraction", extraction)
	target_name = str(target_name or "").strip()
	if not target_name or not frappe.db.exists(extraction.target_doctype, target_name):
		frappe.throw(frappe._("The target document does not exist."))
	layout = str(extraction.get("selected_layout") or "").strip()
	if not layout:
		return None
	if frappe.db.get_value("Document Scanner Layout", layout, "layout_doctype") != extraction.target_doctype:
		frappe.throw(frappe._("The selected scanner layout does not belong to the target DocType."))

	existing_details = {
		row.scanned_document_detail: frappe.get_doc("Scanned Document Detail", row.scanned_document_detail)
		for row in extraction.source_files if row.scanned_document_detail
	}
	existing_scans = sorted({row.scanner_document for row in existing_details.values() if row.scanner_document})
	if existing_scans:
		primary_scan = existing_scans[0]
		for scan_name in existing_scans:
			create_reference(scan_name, extraction.target_doctype, target_name)
			# One historical scan may be referenced by more than one target. Keep
			# its legacy primary owner unchanged; the immutable reference table is
			# the source of truth for additional targets.
			frappe.db.set_value(
				"Scanned Document", scan_name, "scanner_layout", layout, update_modified=False
			)
	else:
		scanned = frappe.new_doc("Scanned Document")
		scanned._doctype = extraction.target_doctype
		scanned._docname = target_name
		scanned.scanner_layout = layout
		scanned.insert(ignore_permissions=True)
		primary_scan = scanned.name

	for detail in existing_details.values():
		if not detail.scanner_document:
			frappe.db.set_value("Scanned Document Detail", detail.name, {
				"scanner_document": primary_scan,
				"is_deleted": 0,
			}, update_modified=False)
	for page in extraction.pages:
		values = {
			"scanner_document": primary_scan,
			"page_no": cint(page.page_no),
			"layout_type": page.layout_type,
			"page_type": page.page_type,
			"title": page.layout_section,
			"is_deleted": 0,
		}
		if page.scanned_document_detail:
			frappe.db.set_value("Scanned Document Detail", page.scanned_document_detail, values, update_modified=False)
			continue
		detail = frappe.get_doc({
			"doctype": "Scanned Document Detail",
			**values,
			"attachment": page.image,
		})
		detail.insert(ignore_permissions=True)
		page.scanned_document_detail = detail.name
	return primary_scan


def finalize_extraction_target(extraction, target_name, *, status="Created", stage="Target Document"):
	"""Atomically connect evidence and mark one extraction target complete."""
	if isinstance(extraction, str):
		extraction = frappe.get_doc("Document Extraction", extraction)
	scanned_document = link_extraction_to_target(extraction, target_name)
	extraction.created_document = target_name
	extraction.scanned_document = scanned_document
	extraction.status = status
	extraction.processing_completed_on = now_datetime()
	if hasattr(extraction, "add_processing_event"):
		extraction.add_processing_event(
			stage,
			status,
			f"{status} {extraction.target_doctype} {target_name} with preserved scan lineage.",
			{"doctype": extraction.target_doctype, "name": target_name, "scanned_document": scanned_document},
		)
	extraction.flags.skip_auto_process = True
	extraction.save(ignore_permissions=True)
	return {
		"doctype": extraction.target_doctype,
		"docname": target_name,
		"scanned_document": scanned_document,
	}


def target_after_insert(doc, method=None):
	if doc.doctype in INTERNAL or not doc.meta.has_field("amended_from") or not doc.get("amended_from"):
		return
	previous = frappe.get_all("Scanned Document Reference", filters={"target_doctype": doc.doctype,
		"target_name": doc.amended_from}, fields=["name", "scanned_document"], order_by="creation desc")
	for old in previous:
		frappe.db.set_value("Scanned Document Reference", old.name, {"active": 0, "status": "Amended"}, update_modified=False)
		create_reference(old.scanned_document, doc.doctype, doc.name, old.name)
		frappe.db.set_value("Scanned Document", old.scanned_document, {"_doctype": doc.doctype, "_docname": doc.name}, update_modified=False)


def target_on_cancel(doc, method=None):
	if doc.doctype in INTERNAL:
		return
	for name in frappe.get_all("Scanned Document Reference", filters={"target_doctype": doc.doctype,
		"target_name": doc.name, "active": 1}, pluck="name"):
		frappe.db.set_value("Scanned Document Reference", name, {"active": 0, "status": "Cancelled"}, update_modified=False)

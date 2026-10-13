# Copyright (c) 2025, sakthi123msd@gmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

DETAIL = "Scanned Document Detail"
SCAN = "Scanned Document"


class ScannedDocumentDetail(Document):
	def on_trash(self) -> None:
		# Core deletes attached Files after on_trash. Rehome any still in use.
		for file in frappe.get_all("File", filters={
			"attached_to_doctype": DETAIL, "attached_to_name": self.name,
		}, fields=["name", "file_url"]):
			_retain_live_file(file, self.name)


def attach_files(detail: Document) -> None:
	"""Keep File ownership aligned with the fields consumed by S3 migration."""
	for field in ("attachment", "original_attachment"):
		url = detail.get(field)
		if url:
			name = _attachment_file(detail.name, field, url)
			if not name:
				continue
			frappe.db.set_value("File", name, {
				"attached_to_doctype": DETAIL, "attached_to_name": detail.name,
				"attached_to_field": field,
			})


def _attachment_file(detail_name, field, url):
	"""Return the File for url owned by this detail or its scan, preferring this detail and field."""
	scan = frappe.db.get_value(DETAIL, detail_name, "scanner_document")
	files = frappe.get_all("File", filters={"file_url": url}, order_by="creation",
		fields=["name", "attached_to_doctype", "attached_to_name", "attached_to_field"])
	owned = [file for file in files if _is_scan_file(file, detail_name, scan)]
	if not owned:
		return None
	return min(owned, key=lambda file: (file.attached_to_name != detail_name, file.attached_to_field != field)).name


def _is_scan_file(file, detail_name, scan):
	if file.attached_to_doctype == DETAIL:
		return file.attached_to_name == detail_name or bool(
			scan and frappe.db.get_value(DETAIL, file.attached_to_name, "scanner_document") == scan)
	return bool(scan) and file.attached_to_doctype == SCAN and file.attached_to_name == scan


def _retain_live_file(file, deleted_name):
	for field in ("attachment", "original_attachment"):
		live = frappe.db.get_value(DETAIL, {
			field: file.file_url, "is_deleted": 0, "name": ["!=", deleted_name],
		}, "name")
		if live:
			frappe.db.set_value("File", file.name, {
				"attached_to_doctype": DETAIL, "attached_to_name": live, "attached_to_field": field,
			})
			return


@frappe.whitelist()
def remove_old_deletable_documents() -> None:
	for name in frappe.get_all(DETAIL, filters={"is_deleted": 1}, pluck="name"):
		frappe.delete_doc(DETAIL, name)

# Copyright (c) 2025, sakthi123msd@gmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ScannedDocumentDetail(Document):
	pass

@frappe.whitelist()
def remove_old_deletable_documents():
	doc = frappe.get_all("Scanned Document Detail", filters = {
		"is_deleted" : 1
	}, pluck = 'name')

	for i in doc:
		doc = frappe.get_doc("Scanned Document Detail", i)
		doc.delete()

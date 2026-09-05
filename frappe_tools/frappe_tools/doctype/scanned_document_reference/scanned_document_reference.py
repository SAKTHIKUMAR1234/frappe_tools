import frappe
from frappe.model.document import Document


class ScannedDocumentReference(Document):
	def validate(self):
		if self.active:
			other = frappe.db.exists("Scanned Document Reference", {"scanned_document": self.scanned_document,
				"active": 1, "name": ["!=", self.name or ""]})
			if other:
				frappe.throw("A scan can have only one active business-document version.")

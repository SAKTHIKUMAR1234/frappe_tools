import frappe
from frappe.model.document import Document


class DocumentExtraction(Document):
	def set_status(self, status, commit=False):
		self.db_set("status", status)
		if commit:
			frappe.db.commit()

import frappe
from frappe.model.document import Document


class DocumentAutomationMemory(Document):
	def validate(self):
		if not self.target_doctype or not frappe.db.exists("DocType", self.target_doctype):
			frappe.throw(frappe._("A valid Target DocType is required."))
		meta = frappe.get_meta(self.target_doctype)
		if not self.target_field or not meta.has_field(self.target_field):
			frappe.throw(frappe._("{0} is not a field on {1}.").format(self.target_field, self.target_doctype))
		self.confirmations = max(int(self.confirmations or 0), 0)
		self.contradictions = max(int(self.contradictions or 0), 0)
		self.confidence = min(max(float(self.confidence or 0), 0), 1)

import frappe
from frappe.model.document import Document


class DocumentRuleBook(Document):
	def validate(self):
		self._validate_field_rules()

	def _validate_field_rules(self):
		"""Ensure each field rule points at a real field on the target DocType."""
		if not self.target_doctype or not self.field_rules:
			return

		meta = frappe.get_meta(self.target_doctype)
		valid = {df.fieldname for df in meta.fields}
		for row in self.field_rules:
			if row.fieldname and row.fieldname not in valid:
				frappe.throw(
					frappe._("Row {0}: '{1}' is not a field on {2}.").format(
						row.idx, row.fieldname, self.target_doctype
					)
				)
			if row.fieldname and not row.label:
				row.label = meta.get_label(row.fieldname)

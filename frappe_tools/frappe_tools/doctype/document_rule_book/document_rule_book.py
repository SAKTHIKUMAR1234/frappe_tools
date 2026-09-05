import json

import frappe
from frappe.model.document import Document


class DocumentRuleBook(Document):
	def validate(self):
		self._validate_field_rules()
		self._validate_validation_rules()

	def _validate_validation_rules(self):
		if not self.get("validation_rules"):
			return
		try:
			rules = json.loads(self.validation_rules)
		except (TypeError, ValueError) as exc:
			frappe.throw(frappe._("Validation Rules is invalid JSON: {0}").format(exc))
		if not isinstance(rules, list):
			frappe.throw(frappe._("Validation Rules must be a JSON array."))
		from frappe_tools.extractors.validation import SUPPORTED
		for index, rule in enumerate(rules, 1):
			if not isinstance(rule, dict) or rule.get("type") not in SUPPORTED:
				frappe.throw(frappe._("Validation rule {0} has an unsupported type.").format(index))

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
			self._validate_resolver(row, meta)

	def _validate_resolver(self, row, meta):
		resolver = row.get("resolver_type") or "None"
		if resolver not in {"None", "Exact", "Search"}:
			frappe.throw(frappe._("Row {0}: unsupported resolver {1}.").format(row.idx, resolver))
		if row.get("minimum_confidence") is not None and not 0 <= float(row.minimum_confidence) <= 1:
			frappe.throw(frappe._("Row {0}: Minimum Confidence must be between 0 and 1.").format(row.idx))
		if row.get("resolver_filters"):
			try:
				filters = json.loads(row.resolver_filters)
			except (TypeError, ValueError) as exc:
				frappe.throw(frappe._("Row {0}: Resolver Filters is invalid JSON: {1}").format(row.idx, exc))
			if not isinstance(filters, dict):
				frappe.throw(frappe._("Row {0}: Resolver Filters must be a JSON object.").format(row.idx))
		if resolver == "None" or not row.fieldname:
			return
		df = meta.get_field(row.fieldname)
		if not df or df.fieldtype != "Link" or not df.options:
			frappe.throw(frappe._("Row {0}: resolvers are allowed only on Link fields.").format(row.idx))
		linked = frappe.get_meta(df.options)
		for fieldname in [x.strip() for x in (row.get("resolver_match_fields") or "").split(",") if x.strip()]:
			if fieldname != "name" and not linked.has_field(fieldname):
				frappe.throw(frappe._("Row {0}: {1} is not a field on {2}.").format(row.idx, fieldname, df.options))

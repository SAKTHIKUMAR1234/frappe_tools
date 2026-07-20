import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class I2AAction(Document):
	def validate(self):
		self._validate_roles()
		self._validate_schema()

	def _validate_roles(self):
		orchestrators = [m for m in self.models if cint(m.is_orchestrator)]
		if len(orchestrators) != 1:
			frappe.throw(_("Exactly one model row must be flagged Orchestrator (found {0}).").format(len(orchestrators)))

		verifiers = [m for m in self.models if cint(m.is_verifier)]
		if len(verifiers) > 1:
			frappe.throw(_("At most one model row may be flagged Verifier."))

		for row in self.models:
			if not cint(frappe.db.get_value("AI Model", row.ai_model, "enabled")):
				frappe.throw(_("AI Model {0} is disabled — enable it or remove the row.").format(row.ai_model))

		schema = self.parsed_schema()
		needs_vision = any(f.get("bbox_required") for f in schema)
		if needs_vision and self.models:
			has_vision = any(
				cint(frappe.db.get_value("AI Model", row.ai_model, "supports_vision")) for row in self.models
			)
			if not has_vision:
				frappe.throw(_("This action extracts from images but no linked AI Model has Supports Vision enabled."))

	def _validate_schema(self):
		try:
			schema = self.parsed_schema()
		except Exception as exc:
			frappe.throw(_("Output Schema is not valid JSON: {0}").format(exc))
		if not isinstance(schema, list):
			frappe.throw(_("Output Schema must be a JSON list of field objects."))
		for f in schema:
			if not isinstance(f, dict) or not f.get("key"):
				frappe.throw(_("Every Output Schema entry needs at least a \"key\"."))

	def parsed_schema(self):
		if not self.output_schema:
			return []
		return json.loads(self.output_schema)

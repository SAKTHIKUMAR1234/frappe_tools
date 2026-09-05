import frappe
from frappe import _
from frappe.model.document import Document


class AIModel(Document):
	def validate(self):
		if self.provider in ("Codex OAuth", "Claude OAuth"):
			self.auth_type = "Managed OAuth"
			self.api_key = None
			self.api_secret = None
			if self.agent_executable and not str(self.agent_executable).startswith("/"):
				frappe.throw(_("Agent Executable must be an absolute path or left empty."))
			if self.fallback_model == self.name:
				frappe.throw(_("Fallback Model cannot refer to itself."))
			return
		if self.provider not in ("OpenRouter", "OpenAI", "Codex OAuth", "Claude OAuth"):
			frappe.msgprint(
				_("Provider {0} has no built-in transport adapter yet.").format(
					self.provider
				),
				indicator="orange",
				alert=True,
			)

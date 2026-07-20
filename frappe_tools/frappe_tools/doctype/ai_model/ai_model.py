import frappe
from frappe import _
from frappe.model.document import Document


class AIModel(Document):
	def validate(self):
		if self.provider != "OpenRouter":
			frappe.msgprint(
				_("Provider {0} has no transport adapter yet — only OpenRouter calls work today.").format(
					self.provider
				),
				indicator="orange",
				alert=True,
			)

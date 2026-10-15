import frappe
from frappe.model.document import Document


class AIModel(Document):
	@property
	def is_usable(self) -> bool:
		"""True when this model and its account are enabled."""
		return bool(self.enabled and frappe.db.get_value("AI Provider Account", self.account, "enabled"))

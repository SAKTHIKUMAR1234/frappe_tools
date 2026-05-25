import frappe
from frappe.model.document import Document


class DocumentExtractionSettings(Document):
	def get_api_key(self):
		"""Return the decrypted OpenRouter API key, or None if unset."""
		if not self.openrouter_api_key:
			return None
		return self.get_password("openrouter_api_key")

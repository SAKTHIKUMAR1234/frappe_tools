import frappe
from frappe.model.document import Document

AUTH_TYPES = {
	"OpenRouter": ("API Key",),
	"OpenAI Codex": ("ChatGPT Login",),
	"Anthropic": ("API Key", "Setup Token"),
}


class AIProviderAccount(Document):
	def validate(self) -> None:
		if self.auth_type not in AUTH_TYPES.get(self.provider, ()):
			frappe.throw(frappe._("Invalid authentication type for this provider"))
		if self.auth_type == "Setup Token":
			token = self.get_password("api_key", raise_exception=False) or ""
			if token and not token.startswith("sk-ant-oat"):
				frappe.throw(frappe._("Anthropic setup tokens must start with sk-ant-oat"))


def has_permission(doc: Document, ptype: str, user: str | None = None) -> bool:
	"""Keep credentials restricted even when a role grants global read access."""
	return "System Manager" in frappe.get_roles(user or frappe.session.user)


def permission_query_conditions(user: str | None = None) -> str:
	return "" if "System Manager" in frappe.get_roles(user or frappe.session.user) else "1=0"

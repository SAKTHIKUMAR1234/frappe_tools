import frappe
from frappe.model.document import Document

from frappe_tools.ai.providers import ProviderError, genie

AUTH_TYPES = {
	"OpenRouter": ("API Key",),
	"OpenAI Codex": ("ChatGPT Login",),
	"Anthropic": ("API Key", "Setup Token"),
	"Genie": ("Shared Secret",),
}
GENIE_SECRET_LENGTH = 32


class AIProviderAccount(Document):
	def validate(self) -> None:
		if self.auth_type not in AUTH_TYPES.get(self.provider, ()):
			frappe.throw(frappe._("Invalid authentication type for this provider"))
		if self.auth_type == "Setup Token":
			token = self.get_password("api_key", raise_exception=False) or ""
			if token and not token.startswith("sk-ant-oat"):
				frappe.throw(frappe._("Anthropic setup tokens must start with sk-ant-oat"))
		if self.provider == "Genie":
			self.validate_genie()

	def validate_genie(self) -> None:
		try:
			genie.check_url(self.base_url)
		except ProviderError as error:
			frappe.throw(str(error))
		secret = self.get_password("api_key", raise_exception=False) or ""
		if len(secret) < GENIE_SECRET_LENGTH:
			frappe.throw(frappe._("The Genie secret must hold at least {0} characters").format(GENIE_SECRET_LENGTH))
		if (self.get("request_timeout") or 0) < 0:
			frappe.throw(frappe._("Request Timeout cannot be negative"))


def has_permission(doc: Document, ptype: str, user: str | None = None) -> bool:
	"""Keep credentials restricted even when a role grants global read access."""
	return "System Manager" in frappe.get_roles(user or frappe.session.user)


def permission_query_conditions(user: str | None = None) -> str:
	return "" if "System Manager" in frappe.get_roles(user or frappe.session.user) else "1=0"

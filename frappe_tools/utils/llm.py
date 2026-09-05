"""Configured vision transport, independent of the document decision agent."""

import json
import re

import frappe
from frappe import _

SETTINGS_DOCTYPE = "Document Extraction Settings"
DEFAULT_VISION_MODEL = "Document Automation Luna"


def get_settings():
	return frappe.get_single(SETTINGS_DOCTYPE)


def ensure_ready():
	"""Resolve the explicitly selected vision model without switching providers."""
	settings = get_settings()
	if not _setting(settings, "enable"):
		frappe.throw(_("Document Extraction is disabled. Enable it in Document Extraction Settings."))
	model_name = _setting(settings, "vision_ai_model") or DEFAULT_VISION_MODEL
	if not frappe.db.exists("AI Model", model_name):
		frappe.throw(_("Vision AI Model {0} does not exist").format(model_name))
	model = frappe.get_doc("AI Model", model_name)
	if not model.enabled:
		frappe.throw(_("Vision AI Model {0} is disabled").format(model_name))
	if model.provider not in {"OpenRouter", "Codex OAuth"}:
		frappe.throw(_("Select an OpenRouter or Codex OAuth vision model."))
	if model.provider == "OpenRouter" and not model.get_password("api_key", raise_exception=False):
		frappe.throw(_("Configure the API key on vision model {0}.").format(model_name))
	if not model.supports_vision:
		frappe.throw(_("Vision is not enabled on AI Model {0}").format(model_name))
	if model.fallback_model:
		frappe.throw(_("Remove the fallback model from {0}; document extraction must fail closed.").format(model_name))
	return settings, model


def readiness():
	"""Return configuration readiness without contacting an external API."""
	try:
		_settings, model = ensure_ready()
		from frappe_tools.automation import agent_providers

		status = agent_providers.health(model) if model.provider == "Codex OAuth" else {"ready": True}
		return {
			"ready": bool(status.get("ready")),
			"provider": model.provider,
			"model": model.model_id or model.name,
			"reason": status.get("reason"),
		}
	except Exception as exc:
		return {"ready": False, "provider": None, "model": None, "reason": str(exc).split("\n", 1)[0]}


def call_vision(image_data_urls, system_prompt, user_prompt, *, extraction=None, target_doctype=None):
	"""Extract through the selected provider; the decision model is not consulted."""
	_settings, model = ensure_ready()
	if model.provider == "OpenRouter":
		from frappe_tools.i2a import providers

		urls = list(image_data_urls or [])
		if not urls or len(urls) > 24:
			frappe.throw(_("Document vision requires between 1 and 24 page images."))
		if any(not isinstance(url, str) or not url.startswith("data:image/") for url in urls):
			frappe.throw(_("Document vision accepts uploaded image data only."))
		messages = [
			{"role": "system", "content": system_prompt},
			{"role": "user", "content": [
				{"type": "text", "text": user_prompt},
				*[{"type": "image_url", "image_url": {"url": url}} for url in urls],
			]},
		]
		result = providers.call_model(model, messages, purpose="document_vision", run=extraction, action=target_doctype)
		if not isinstance(result.get("data"), dict):
			raise providers.ProviderError(_("Vision returned invalid document JSON; review or retry this scan."))
		return {**result, "model": model.model_id or model.name}
	from frappe_tools.automation import agent_providers

	return agent_providers.call_vision(
		model,
		image_data_urls,
		system_prompt,
		user_prompt,
		run=extraction,
		action=target_doctype,
	)


def _setting(settings, name, default=None):
	getter = getattr(settings, "get", None)
	if callable(getter):
		return getter(name, default)
	return getattr(settings, name, default)


def safe_json_loads(text):
	"""Best-effort parse of an LLM JSON response.

	Handles markdown code fences, leading/trailing prose, and trailing commas.
	Returns the parsed object or None.
	"""
	if not text:
		return None
	text = text.strip()

	# Strip ```json ... ``` fences.
	fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
	if fence:
		text = fence.group(1).strip()

	try:
		return json.loads(text)
	except Exception:
		pass

	# Fall back to the first balanced {...} or [...] block.
	for opener, closer in (("{", "}"), ("[", "]")):
		start = text.find(opener)
		end = text.rfind(closer)
		if start != -1 and end != -1 and end > start:
			candidate = text[start : end + 1]
			candidate = re.sub(r",\s*([}\]])", r"\1", candidate)  # drop trailing commas
			try:
				return json.loads(candidate)
			except Exception:
				continue
	return None

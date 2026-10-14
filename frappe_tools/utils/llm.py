"""JSON parsing, logging and compatibility for document AI callers."""

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt

SETTINGS_DOCTYPE = "Document Extraction Settings"
CALL_LOG_DOCTYPE = "Document AI Call Log"


def get_settings():
	return frappe.get_single(SETTINGS_DOCTYPE)


def ensure_ready():
	"""Return settings after validating the feature is usable, else throw."""
	settings = get_settings()
	if not settings.enable:
		frappe.throw(_("Document Extraction is disabled. Enable it in Document Extraction Settings."))
	return settings


def call_vision(
	image_data_urls: list[str], system_prompt: str, user_prompt: str, *,
	extraction: str | None = None, target_doctype: str | None = None,
	phase: str | None = None, reference_doctype: str | None = None,
	reference_name: str | None = None, run_id: str | None = None, min_output_tokens: int = 0,
	task: str = "Document Extraction", deadline: float | None = None,
) -> dict:
	"""Call the configured task model with images; no request runs past `deadline` (time.monotonic())."""
	from frappe_tools.ai.client import call_model

	return call_model(
		task,
		system_prompt,
		user_prompt,
		images=image_data_urls,
		extraction=extraction,
		target_doctype=target_doctype,
		phase=phase,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		run_id=run_id,
		min_output_tokens=min_output_tokens,
		deadline=deadline,
	)


def log_call(
	extraction: str | None, target_doctype: str | None, model: str | None, status: str,
	http_status: int | None, latency_ms: int, usage: dict | None, body: dict, result, error: str | None,
	raw_text: str | None = None, finish_reason: str | None = None, *,
	phase: str | None = None, reference_doctype: str | None = None, reference_name: str | None = None,
	run_id: str | None = None, provider: str = "OpenRouter",
	secrets: list[str] | None = None,
) -> None:
	"""Insert a Document AI Call Log row; insert errors propagate to the caller."""
	log = frappe.new_doc(CALL_LOG_DOCTYPE)
	log.extraction = extraction
	log.target_doctype = target_doctype
	log.model = model
	log.provider = provider
	log.phase = phase
	log.reference_doctype = reference_doctype
	log.reference_name = reference_name
	log.run_id = run_id
	log.status = status
	log.http_status = http_status
	log.latency_ms = latency_ms
	log.prompt_tokens = cint((usage or {}).get("prompt_tokens"))
	log.completion_tokens = cint((usage or {}).get("completion_tokens"))
	log.total_tokens = cint((usage or {}).get("total_tokens"))
	log.cost_usd = flt((usage or {}).get("cost"))
	log.request_payload = frappe.as_json(redact_secrets(body, secrets))
	log.response_payload = frappe.as_json(redact_secrets(result, secrets))
	log.raw_response = redact_secrets(raw_text or "", secrets)[:140000]
	log.finish_reason = finish_reason
	log.error_message = redact_secrets(error or "", secrets)[:500]
	log.flags.ignore_permissions = True
	log.insert(ignore_permissions=True)


def redact_secrets(value, secrets=None):
	"""Redact credentials and image data recursively from log and error values."""
	if isinstance(value, dict):
		return {
			key: "<redacted>" if _sensitive(key, value) else redact_secrets(item, secrets)
			for key, item in value.items()
		}
	if isinstance(value, list):
		return [redact_secrets(item, secrets) for item in value]
	if isinstance(value, str):
		for secret in secrets or []:
			if secret:
				value = value.replace(secret, "<redacted>")
		value = re.sub(r"data:image/[^\s\"']+", "<image redacted>", value)
		value = re.sub(r"(?:sk-[\w-]+|eyJ[\w-]+\.[\w-]+\.[\w-]+)", "<redacted>", value)
	return value


def _sensitive(key, value):
	if key.lower().replace("-", "_") in {
		"headers",
		"authorization",
		"api_key",
		"x_api_key",
		"access_token",
		"refresh_token",
		"id_token",
	}:
		return True
	return key == "data" and value.get("type") == "base64"


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
	except (ValueError, TypeError):
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
			except (ValueError, TypeError):
				continue
	return None

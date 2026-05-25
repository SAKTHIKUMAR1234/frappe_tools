"""Low-level OpenRouter vision client for document extraction.

Keeps all HTTP/transport concerns (request building, timeout, retries, cost &
token accounting, call logging) separate from the domain logic in
`frappe_tools.api.doc_extract`. Provider is OpenRouter today; the function
surface is provider-agnostic so a future Tally/Odoo path can reuse it.
"""

import json
import re
import time

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SETTINGS_DOCTYPE = "Document Extraction Settings"
CALL_LOG_DOCTYPE = "Document AI Call Log"


def get_settings():
	return frappe.get_single(SETTINGS_DOCTYPE)


def ensure_ready():
	"""Return settings after validating the feature is usable, else throw."""
	settings = get_settings()
	if not settings.enable:
		frappe.throw(_("Document Extraction is disabled. Enable it in Document Extraction Settings."))
	if not settings.get_api_key():
		frappe.throw(_("OpenRouter API Key is not set in Document Extraction Settings."))
	return settings


def call_vision(image_data_urls, system_prompt, user_prompt, *, extraction=None, target_doctype=None):
	"""Call the configured vision model with N images + a JSON-mode prompt.

	Retries transient failures up to `max_attempts` (from settings). Every
	attempt writes a Document AI Call Log row. Returns a dict on success:
	{"data": <parsed json>, "usage": {...}, "model": str, "latency_ms": int}.
	Throws if all attempts fail.
	"""
	settings = ensure_ready()
	api_key = settings.get_api_key()
	model = settings.model or "google/gemini-2.5-flash"
	max_attempts = max(cint(settings.max_attempts) or 1, 1)

	content = [{"type": "text", "text": user_prompt}]
	for url in image_data_urls:
		content.append({"type": "image_url", "image_url": {"url": url}})

	body = {
		"model": model,
		"messages": [
			{"role": "system", "content": system_prompt},
			{"role": "user", "content": content},
		],
		"temperature": flt(settings.temperature) or 0,
		"max_tokens": cint(settings.max_tokens) or 8192,
		"response_format": {"type": "json_object"},
		"usage": {"include": True},
	}
	headers = {
		"Authorization": f"Bearer {api_key}",
		"Content-Type": "application/json",
		"HTTP-Referer": frappe.utils.get_url(),
		"X-Title": "Frappe Tools Document Extraction",
	}
	timeout = cint(settings.request_timeout) or 120

	last_error = None
	for attempt in range(1, max_attempts + 1):
		outcome = _attempt_call(body, headers, timeout, model, extraction, target_doctype)
		if outcome.get("ok"):
			return {
				"data": outcome["data"],
				"usage": outcome["usage"],
				"model": model,
				"latency_ms": outcome["latency_ms"],
			}
		last_error = outcome.get("error")
		if attempt < max_attempts:
			time.sleep(min(2 * attempt, 8))

	frappe.throw(_("LLM extraction failed after {0} attempt(s): {1}").format(max_attempts, last_error or _("unknown error")))


def _attempt_call(body, headers, timeout, model, extraction, target_doctype):
	started = time.monotonic()
	status = "Success"
	http_status = None
	error = None
	result = {}
	usage = {}
	parsed = None

	try:
		resp = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=timeout)
		http_status = resp.status_code
		try:
			result = resp.json()
		except ValueError:
			result = {"non_json_response": (resp.text or "")[:4000]}
		resp.raise_for_status()

		if isinstance(result, dict) and result.get("error"):
			raise RuntimeError(str(result["error"]))

		usage = (result.get("usage") or {}) if isinstance(result, dict) else {}
		choices = result.get("choices") or []
		raw_text = (choices[0].get("message", {}).get("content") if choices else "") or ""
		parsed = safe_json_loads(raw_text)
		if parsed is None:
			raise ValueError("could not parse JSON from model response")
	except requests.Timeout as exc:
		status, error = "Timeout", str(exc)
	except requests.HTTPError as exc:
		status = "Error"
		error = _extract_api_error(result) or str(exc)
	except Exception as exc:
		status = "Error"
		error = _extract_api_error(result) or str(exc)

	latency_ms = int((time.monotonic() - started) * 1000)
	_log_call(extraction, target_doctype, model, status, http_status, latency_ms, usage, body, result, error)

	if status == "Success" and parsed is not None:
		return {"ok": True, "data": parsed, "usage": usage, "latency_ms": latency_ms}
	return {"ok": False, "error": error}


def _extract_api_error(result):
	if isinstance(result, dict):
		err = result.get("error")
		if isinstance(err, dict):
			return err.get("message") or json.dumps(err)[:500]
		if err:
			return str(err)[:500]
	return None


def _log_call(extraction, target_doctype, model, status, http_status, latency_ms, usage, body, result, error):
	"""Persist a Document AI Call Log row. Never raises into the caller."""
	try:
		log = frappe.new_doc(CALL_LOG_DOCTYPE)
		log.extraction = extraction
		log.target_doctype = target_doctype
		log.model = model
		log.provider = "OpenRouter"
		log.status = status
		log.http_status = http_status
		log.latency_ms = latency_ms
		log.prompt_tokens = cint((usage or {}).get("prompt_tokens"))
		log.completion_tokens = cint((usage or {}).get("completion_tokens"))
		log.total_tokens = cint((usage or {}).get("total_tokens"))
		log.cost_usd = flt((usage or {}).get("cost"))
		log.request_payload = frappe.as_json(_redact_images(body))
		log.response_payload = frappe.as_json(result)[:140000]
		log.error_message = (error or "")[:500]
		log.flags.ignore_permissions = True
		log.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Document AI Call Log insert failed")


def _redact_images(body):
	"""Deep-copy the request body with image data URLs replaced by a stub."""
	clone = json.loads(json.dumps(body))
	for message in clone.get("messages", []):
		content = message.get("content")
		if isinstance(content, list):
			for part in content:
				if isinstance(part, dict) and part.get("type") == "image_url":
					url = (part.get("image_url") or {}).get("url", "")
					part["image_url"] = {"url": f"<image redacted: {len(url)} chars>"}
	return clone


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

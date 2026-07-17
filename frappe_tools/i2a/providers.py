"""Provider transport layer for the I2A engine.

One job: given an `AI Model` document, messages and options, make the HTTP
call, account tokens/cost, and log EVERY physical attempt as its own
`I2A LLM Call` row (transport retries and truncation re-asks included —
billing truth beats tidy grouping). Dispatches on `AI Model.provider`;
only OpenRouter ships an adapter today.
"""

import json
import time

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt

from frappe_tools.utils.llm import safe_json_loads

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TRANSPORT_ATTEMPTS = 3
TRUNCATION_RETRY_MAX_TOKENS = 16384


class ProviderError(Exception):
	pass


class BudgetExceeded(ProviderError):
	"""Deliberate stop: max_calls_per_run or run_seconds_budget reached.
	Lives here (not in engine) so phase code can catch it SEPARATELY from
	transport failures without a circular import — a budget stop must never
	be logged as a provider error."""


def call_model(ai_model, messages, *, json_mode=True, purpose="", run=None, action=None, max_tokens=None):
	"""Call `ai_model` (an AI Model doc or name) with `messages`.

	Returns {"data": parsed, "raw_text": str, "usage": dict, "latency_ms": int}.
	Raises ProviderError when all attempts fail. Retries transient transport
	failures up to TRANSPORT_ATTEMPTS, and retries ONE truncated reply at a
	raised token ceiling — each physical attempt is its own I2A LLM Call row.
	"""
	if isinstance(ai_model, str):
		ai_model = frappe.get_doc("AI Model", ai_model)
	if not cint(ai_model.enabled):
		raise ProviderError(_("AI Model {0} is disabled").format(ai_model.name))
	if ai_model.provider != "OpenRouter":
		raise ProviderError(
			_("Provider {0} has no adapter yet — only OpenRouter is supported").format(ai_model.provider)
		)

	tokens = cint(max_tokens) or cint(ai_model.max_tokens) or 8192
	last_error = None

	for attempt in range(1, TRANSPORT_ATTEMPTS + 1):
		outcome = _openrouter_attempt(
			ai_model, messages, json_mode=json_mode, max_tokens=tokens,
			purpose=purpose, run=run, action=action,
		)
		if outcome.get("ok"):
			return outcome

		last_error = outcome.get("error")

		if outcome.get("truncated") and tokens < TRUNCATION_RETRY_MAX_TOKENS:
			# One escalation: same request, bigger budget, logged separately.
			tokens = TRUNCATION_RETRY_MAX_TOKENS
			continue
		if not outcome.get("transient"):
			break
		if attempt < TRANSPORT_ATTEMPTS:
			time.sleep(min(2 * attempt, 8))

	raise ProviderError(last_error or _("unknown provider error"))


def call_with_tools(ai_model, messages, tool_specs, *, purpose="", run=None, action=None, max_tokens=None, tool_choice="auto"):
	"""Function-calling call. Returns {"content", "tool_calls", "finish_reason",
	"message"} where tool_calls is a list of {"id","name","arguments"(dict)}.
	Raises ProviderError when all transport attempts fail. Logs every attempt."""
	if isinstance(ai_model, str):
		ai_model = frappe.get_doc("AI Model", ai_model)
	if not cint(ai_model.enabled):
		raise ProviderError(_("AI Model {0} is disabled").format(ai_model.name))
	if ai_model.provider != "OpenRouter":
		raise ProviderError(_("Provider {0} has no adapter yet").format(ai_model.provider))

	api_key = ai_model.get_password("api_key", raise_exception=False)
	if not api_key:
		raise ProviderError(_("AI Model {0} has no API key set").format(ai_model.name))

	tokens = cint(max_tokens) or cint(ai_model.max_tokens) or 8192
	body = {
		"model": ai_model.model_id,
		"messages": messages,
		"temperature": flt(ai_model.temperature) or 0,
		"max_tokens": tokens,
		"tools": tool_specs,
		"tool_choice": tool_choice,
		"usage": {"include": True},
	}
	headers = {
		"Authorization": f"Bearer {api_key}",
		"Content-Type": "application/json",
		"HTTP-Referer": frappe.utils.get_url(),
		"X-Title": "Frappe Tools I2A Engine",
	}
	url = ai_model.base_url or OPENROUTER_URL
	last_error = None

	for attempt in range(1, TRANSPORT_ATTEMPTS + 1):
		started = time.monotonic()
		status, http_status, error, result = "Success", None, None, {}
		transient = False
		try:
			resp = requests.post(url, headers=headers, json=body, timeout=120)
			http_status = resp.status_code
			try:
				result = resp.json()
			except ValueError:
				result = {"non_json_response": (resp.text or "")[:4000]}
			resp.raise_for_status()
			if isinstance(result, dict) and result.get("error"):
				raise RuntimeError(str(result["error"]))
			usage = (result.get("usage") or {}) if isinstance(result, dict) else {}
			first = (result.get("choices") or [{}])[0]
			msg = first.get("message") or {}
			finish = first.get("finish_reason")
			if finish == "length":
				# a truncated tool reply is garbage-in for the whole loop — fail
				# loudly instead of silently continuing with half a tool call
				raise RuntimeError("model reply truncated (finish_reason=length)")
			tool_calls = []
			for tc in msg.get("tool_calls") or []:
				fn = tc.get("function") or {}
				parsed_args = safe_json_loads(fn.get("arguments") or "{}")
				if parsed_args is None:
					# marker key: undeclared → tools.execute drops it, the tool
					# runs on config defaults only, and the trace shows why
					parsed_args = {"__unparseable_arguments__": (fn.get("arguments") or "")[:120]}
				tool_calls.append({
					"id": tc.get("id"),
					"name": fn.get("name"),
					"arguments": parsed_args,
				})
			latency_ms = int((time.monotonic() - started) * 1000)
			cost, est = _cost(ai_model, usage, status)
			_log_call(ai_model=ai_model, purpose=purpose, run=run, action=action, status=status,
				http_status=http_status, latency_ms=latency_ms, usage=usage, cost=cost,
				cost_estimated=est, body=body, result=result, error=None)
			return {
				"content": msg.get("content") or "",
				"tool_calls": tool_calls,
				"finish_reason": finish,
				"message": msg,
			}
		except requests.Timeout as exc:
			status, error, transient = "Timeout", str(exc), True
		except requests.HTTPError as exc:
			status, error = "Error", (_api_error(result) or str(exc))
			transient = bool(http_status and (http_status >= 500 or http_status == 429))
		except Exception as exc:
			status, error = "Error", (_api_error(result) or str(exc))
		latency_ms = int((time.monotonic() - started) * 1000)
		cost, est = _cost(ai_model, {}, status)
		_log_call(ai_model=ai_model, purpose=purpose, run=run, action=action, status=status,
			http_status=http_status, latency_ms=latency_ms, usage={}, cost=cost,
			cost_estimated=est, body=body, result=result, error=error)
		last_error = error
		if not transient or attempt >= TRANSPORT_ATTEMPTS:
			break
		time.sleep(min(2 * attempt, 8))

	raise ProviderError(last_error or _("unknown provider error"))


def _openrouter_attempt(ai_model, messages, *, json_mode, max_tokens, purpose, run, action):
	api_key = ai_model.get_password("api_key", raise_exception=False)
	if not api_key:
		return {"ok": False, "transient": False, "error": _("AI Model {0} has no API key set").format(ai_model.name)}

	body = {
		"model": ai_model.model_id,
		"messages": messages,
		"temperature": flt(ai_model.temperature) or 0,
		"max_tokens": max_tokens,
		"usage": {"include": True},
	}
	if json_mode and cint(ai_model.supports_json_mode):
		body["response_format"] = {"type": "json_object"}

	headers = {
		"Authorization": f"Bearer {api_key}",
		"Content-Type": "application/json",
		"HTTP-Referer": frappe.utils.get_url(),
		"X-Title": "Frappe Tools I2A Engine",
	}
	url = ai_model.base_url or OPENROUTER_URL

	started = time.monotonic()
	status = "Success"
	http_status = None
	error = None
	result = {}
	usage = {}
	parsed = None
	raw_text = ""
	finish_reason = None
	truncated = False
	transient = False

	try:
		resp = requests.post(url, headers=headers, json=body, timeout=120)
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
		first = choices[0] if choices else {}
		raw_text = first.get("message", {}).get("content") or ""
		finish_reason = first.get("finish_reason")

		parsed = safe_json_loads(raw_text)
		if parsed is None:
			truncated = finish_reason == "length" or _looks_truncated(raw_text)
			status = "Error"
			error = _("unparseable model reply (finish_reason={0}). Raw begins: {1}").format(
				finish_reason, (raw_text or "<empty>")[:200]
			)
			# OpenRouter keep-alive artifact: on slow requests it pads the
			# connection with whitespace; if the upstream dies mid-generation the
			# 200 body is ONLY that padding (observed live: 39s, whitespace, no
			# JSON). That is a transient upstream failure — retry it.
			if "non_json_response" in result and not str(result.get("non_json_response") or "").strip():
				transient = True
				error = _("provider returned an empty keep-alive body (upstream died mid-generation) — retrying")
	except requests.Timeout as exc:
		status, error, transient = "Timeout", str(exc), True
	except requests.HTTPError as exc:
		status = "Error"
		error = _api_error(result) or str(exc)
		transient = bool(http_status and (http_status >= 500 or http_status == 429))
	except Exception as exc:
		status = "Error"
		error = _api_error(result) or str(exc)

	latency_ms = int((time.monotonic() - started) * 1000)
	cost, cost_estimated = _cost(ai_model, usage, status)
	_log_call(
		ai_model=ai_model, purpose=purpose, run=run, action=action,
		status=status, http_status=http_status, latency_ms=latency_ms,
		usage=usage, cost=cost, cost_estimated=cost_estimated,
		body=body, result=result, error=error,
	)

	if status == "Success" and parsed is not None:
		return {
			"ok": True,
			"data": parsed,
			"raw_text": raw_text,
			"usage": usage,
			"latency_ms": latency_ms,
		}
	return {"ok": False, "error": error, "transient": transient, "truncated": truncated}


def _looks_truncated(text):
	if not text:
		return False
	return text.count("{") > text.count("}")


def _cost(ai_model, usage, status):
	"""(cost_usd, estimated_flag). Provider-reported cost wins; else rate-card
	estimate; failed calls with no usage carry 0 + estimated flag (they were
	still billed — the flag keeps the report honest)."""
	reported = flt((usage or {}).get("cost"))
	if reported:
		return reported, 0
	pt = cint((usage or {}).get("prompt_tokens"))
	ct = cint((usage or {}).get("completion_tokens"))
	if pt or ct:
		est = (pt / 1_000_000) * flt(ai_model.cost_per_m_input) + (ct / 1_000_000) * flt(
			ai_model.cost_per_m_output
		)
		return est, 1
	return 0, 1 if status != "Success" else 0


def _log_call(*, ai_model, purpose, run, action, status, http_status, latency_ms, usage, cost, cost_estimated, body, result, error):
	"""One I2A LLM Call row per physical HTTP attempt. Never raises."""
	try:
		log = frappe.new_doc("I2A LLM Call")
		log.run = run
		log.action = action
		log.ai_model = ai_model.name
		log.provider = ai_model.provider
		log.purpose = purpose
		log.status = status
		log.http_status = http_status
		log.latency_ms = latency_ms
		log.prompt_tokens = cint((usage or {}).get("prompt_tokens"))
		log.completion_tokens = cint((usage or {}).get("completion_tokens"))
		log.total_tokens = cint((usage or {}).get("total_tokens"))
		log.cost_usd = flt(cost)
		log.cost_estimated = cint(cost_estimated)
		log.request_payload = frappe.as_json(_redact_images(body))[:140000]
		log.response_payload = frappe.as_json(result)[:140000]
		log.error_message = (error or "")[:500]
		log.flags.ignore_permissions = True
		log.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "I2A LLM Call insert failed")


def _api_error(result):
	if isinstance(result, dict):
		err = result.get("error")
		if isinstance(err, dict):
			return err.get("message") or json.dumps(err)[:500]
		if err:
			return str(err)[:500]
	return None


def _redact_images(body):
	clone = json.loads(json.dumps(body))
	for message in clone.get("messages", []):
		content = message.get("content")
		if isinstance(content, list):
			for part in content:
				if isinstance(part, dict) and part.get("type") == "image_url":
					url = (part.get("image_url") or {}).get("url", "")
					part["image_url"] = {"url": f"<image redacted: {len(url)} chars>"}
	return clone

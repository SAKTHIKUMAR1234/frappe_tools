"""Provider transport layer for the I2A engine.

One job: given an `AI Model` document, messages and options, send the call
through the shared frappe_tools.ai provider adapters with the model's AI
Provider Account, account tokens/cost, and log EVERY physical attempt as its
own `I2A LLM Call` row (transport retries and truncation re-asks included —
billing truth beats tidy grouping). Messages and tool specs use the OpenAI
chat shape; each adapter converts them for its provider.
"""

import json
import time

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt
from rq.timeouts import JobTimeoutException

from frappe_tools.ai import client
from frappe_tools.utils.llm import redact_secrets, safe_json_loads

TRANSPORT_ATTEMPTS = 3
TRUNCATION_RETRY_MAX_TOKENS = 16384
REQUEST_TIMEOUT = 120
TRUNCATED_REASONS = ("length", "max_tokens", "incomplete")


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
	ai_model, account = _resolve(ai_model)
	json_mode = bool(json_mode and cint(ai_model.supports_json_mode))
	request = dict(messages=messages, json_mode=json_mode, tools=None, purpose=purpose, run=run, action=action)
	state = _send(ai_model, account, request, max_tokens, escalate=True)
	return {
		"data": state["data"],
		"raw_text": state["raw_text"],
		"usage": state["usage"],
		"latency_ms": state["latency_ms"],
	}


def call_with_tools(ai_model, messages, tool_specs, *, purpose="", run=None, action=None, max_tokens=None):
	"""Function-calling call. `tool_specs` use the OpenAI shape {"type": "function", "function": {...}}.

	Returns {"content", "tool_calls", "finish_reason", "message"} where tool_calls is a list of
	{"id","name","arguments"(dict)} and message is the OpenAI chat assistant message to append.
	Raises ProviderError when all transport attempts fail. Logs every attempt."""
	ai_model, account = _resolve(ai_model)
	tools = [spec["function"] for spec in tool_specs]
	request = dict(messages=messages, json_mode=False, tools=tools, purpose=purpose, run=run, action=action)
	state = _send(ai_model, account, request, max_tokens, escalate=False)
	adapter = client.PROVIDERS[account.provider]
	return {
		"content": state["raw_text"],
		"tool_calls": [_tool_call(call) for call in state["calls"]],
		"finish_reason": state["finish_reason"],
		"message": adapter.assistant_message(state["result"], state["raw_text"]),
	}


def _resolve(ai_model):
	if isinstance(ai_model, str):
		ai_model = frappe.get_doc("AI Model", ai_model)
	if not cint(ai_model.enabled):
		raise ProviderError(_("AI Model {0} is disabled").format(ai_model.name))
	account = frappe.get_doc("AI Provider Account", ai_model.account)
	if not cint(account.enabled):
		raise ProviderError(_("AI Provider Account {0} is disabled").format(account.name))
	return ai_model, account


def _send(ai_model, account, request, max_tokens, *, escalate):
	"""Return the state of the first successful attempt; raise ProviderError when none succeeds."""
	tokens = cint(max_tokens) or cint(ai_model.max_tokens) or 8192
	state = {}
	for attempt in range(1, TRANSPORT_ATTEMPTS + 1):
		state = _attempt(ai_model, account, request, tokens)
		if state["status"] == "Success":
			return state
		if escalate and _is_truncated(state) and tokens < TRUNCATION_RETRY_MAX_TOKENS:
			# One escalation: same request, bigger budget, logged separately.
			tokens = TRUNCATION_RETRY_MAX_TOKENS
			continue
		if not state["transient"]:
			break
		if attempt < TRANSPORT_ATTEMPTS:
			time.sleep(min(2 * attempt, 8))
	raise ProviderError(state.get("error") or _("unknown provider error"))


def _attempt(ai_model, account, request, tokens):
	"""Run one physical request through the shared provider layer and log it."""
	options = {
		"temperature": flt(ai_model.temperature),
		"max_tokens": tokens,
		"timeout": REQUEST_TIMEOUT,
		"json_mode": request["json_mode"],
		"require_json": request["tools"] is None,
	}

	def build(adapter, credentials, model_id):
		system, messages = adapter.chat_messages(request["messages"])
		return adapter.build_turn(credentials, model_id, system, messages, None, options, request["tools"])

	state = client.new_state()
	state["transient"] = False
	started = time.monotonic()
	try:
		client.execute(account, ai_model.model_id, build, options, state)
		if request["tools"] is not None and state["finish_reason"] in TRUNCATED_REASONS:
			# a truncated tool reply is garbage-in for the whole loop — fail
			# loudly instead of silently continuing with half a tool call
			raise ValueError(_("model reply truncated (finish_reason={0})").format(state["finish_reason"]))
		state.update(status="Success", error=None)
	except JobTimeoutException:
		state.update(status="Timeout", error=_("AI job timed out"))
		raise
	except Exception as exc:
		client.record_failure(state, exc)
		state["transient"] = _is_transient(exc, state["http_status"])
	finally:
		state["latency_ms"] = int((time.monotonic() - started) * 1000)
		_log_call(ai_model=ai_model, provider=account.provider, request=request, state=state)
	return state


def _is_transient(exc, http_status):
	"""Network failures, timeouts, rate limits and server errors; an empty keep-alive body is a network failure."""
	return isinstance(exc, requests.RequestException) or http_status == 429 or (http_status or 0) >= 500


def _is_truncated(state):
	return state["data"] is None and (
		state["finish_reason"] in TRUNCATED_REASONS or _looks_truncated(state["raw_text"])
	)


def _tool_call(call):
	arguments = call["arguments"]
	if not isinstance(arguments, dict):
		arguments = safe_json_loads(arguments or "{}")
	if arguments is None:
		# marker key: undeclared → tools.execute drops it, the tool
		# runs on config defaults only, and the trace shows why
		arguments = {"__unparseable_arguments__": (call["arguments"] or "")[:120]}
	return {"id": call["id"], "name": call["name"], "arguments": arguments}


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


def _log_call(*, ai_model, provider, request, state):
	"""One I2A LLM Call row per physical HTTP attempt. Never raises."""
	try:
		usage = state["usage"] or {}
		cost, cost_estimated = _cost(ai_model, usage, state["status"])
		log = frappe.new_doc("I2A LLM Call")
		log.run = request["run"]
		log.action = request["action"]
		log.ai_model = ai_model.name
		log.provider = provider
		log.purpose = request["purpose"]
		log.status = state["status"]
		log.http_status = state["http_status"]
		log.latency_ms = state["latency_ms"]
		log.prompt_tokens = cint(usage.get("prompt_tokens"))
		log.completion_tokens = cint(usage.get("completion_tokens"))
		log.total_tokens = cint(usage.get("total_tokens"))
		log.cost_usd = flt(cost)
		log.cost_estimated = cint(cost_estimated)
		log.request_payload = frappe.as_json(redact_secrets(_redact_images(state["body"]), state["secrets"]))[:140000]
		log.response_payload = frappe.as_json(redact_secrets(state["result"], state["secrets"]))[:140000]
		log.error_message = (state["error"] or "")[:500]
		log.flags.ignore_permissions = True
		log.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "I2A LLM Call insert failed")


def _redact_images(body):
	"""Redact every embedded media payload from persisted provider audits."""
	clone = json.loads(json.dumps(body))
	for message in clone.get("messages", []):
		content = message.get("content")
		if isinstance(content, list):
			for part in content:
				if not isinstance(part, dict):
					continue
				part_type = part.get("type")
				if part_type == "image_url":
					url = (part.get("image_url") or {}).get("url", "")
					part["image_url"] = {"url": f"<image redacted: {len(url)} chars>"}
				elif part_type == "input_audio":
					audio = part.get("input_audio") or {}
					data = str(audio.get("data") or "")
					part["input_audio"] = {
						"data": f"<audio redacted: {len(data)} chars>",
						"format": audio.get("format"),
					}
				elif part_type == "file":
					file_part = part.get("file") or {}
					data = str(file_part.get("file_data") or "")
					part["file"] = {
						"filename": file_part.get("filename"),
						"file_data": f"<file redacted: {len(data)} chars>",
					}
				elif part_type == "video_url":
					video = part.get("video_url") or {}
					url = str(video.get("url") or "")
					part["video_url"] = {"url": f"<video redacted: {len(url)} chars>"}
	return clone

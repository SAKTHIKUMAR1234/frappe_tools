"""Task routing, retries and attempt logs."""

import time

import frappe
import requests
from frappe.utils import cint, flt
from rq.timeouts import JobTimeoutException

from frappe_tools.ai import accounts
from frappe_tools.ai.providers import ProviderError, anthropic, codex, error_message, openrouter
from frappe_tools.utils import llm

TASKS = ("Document Extraction", "Page Classification", "Page Extraction", "Scan Decision", "Verifier")
PROVIDERS = {"OpenRouter": openrouter, "OpenAI Codex": codex, "Anthropic": anthropic}
BODY_CHUNK_BYTES = 8192


class DeadlineReached(TimeoutError):
	"""The time budget of an AI run ended before the next request."""


def call_model(
	task: str,
	system_prompt: str,
	user_prompt: str,
	*,
	images: list[str] | None = None,
	schema: dict | None = None,
	extraction=None,
	target_doctype=None,
	phase=None,
	reference_doctype=None,
	reference_name=None,
	run_id=None,
	min_output_tokens: int = 0,
	ai_model: str | None = None,
	deadline: float | None = None,
) -> dict:
	"""Try the chosen `ai_model`, then the task models in priority order, and log each attempt.

	No request starts after `deadline` (time.monotonic()) and none runs past it.
	"""
	settings = task_settings(task)
	options = task_options(settings, min_output_tokens)
	prompts = (system_prompt, user_prompt, images or [], schema)
	context = dict(
		extraction=extraction,
		target_doctype=target_doctype,
		phase=phase,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		run_id=run_id,
	)
	last_error = None
	for account, model in routes(settings, task, ai_model):
		model_options = route_options(options, model)

		def build(adapter, credentials, model_id):
			return adapter.build_request(credentials, model_id, *prompts, model_options)

		outcome = try_model(account, model.model_id, build, model_options, context, deadline)
		if outcome.get("ok"):
			return outcome["result"]
		last_error = outcome["error"]
	return raise_call_failed(task, last_error)


def raise_call_failed(task: str, last_error: str | None):
	"""Throw the last route error, or ask for an AI Task Models row when the task had no usable route."""
	if last_error is None:
		frappe.throw(
			frappe._(
				"No enabled AI Model for the task {0}. Add an AI Model row for this task in "
				"Document Extraction Settings > AI Task Models."
			).format(task),
			title=frappe._("No AI Model"),
		)
	frappe.throw(frappe._("AI call failed: {0}").format(last_error))


def task_settings(task: str):
	if task not in TASKS:
		frappe.throw(frappe._("Unknown AI task"))
	return llm.ensure_ready()


def task_options(settings, min_output_tokens: int = 0) -> dict:
	return {
		"min_tokens": min_output_tokens,
		"timeout": cint(settings.request_timeout) or 120,
		"attempts": max(cint(settings.max_attempts), 1),
	}


def route_options(options: dict, model) -> dict:
	"""Add the AI Model's temperature and output tokens; the task's token floor still holds."""
	return {
		**options,
		"temperature": flt(model.temperature),
		"max_tokens": max(cint(model.max_tokens) or 8192, options["min_tokens"]),
	}


def routes(settings, task: str, ai_model: str | None = None):
	"""Yield (account, AI Model): the chosen AI Model first, then the enabled task rows."""
	if ai_model:
		yield from _usable_route(ai_model)
	for row in settings.get("task_models") or []:
		if row.task == task and row.enabled:
			yield from _usable_route(row.ai_model)


def _usable_route(ai_model):
	"""Yield the route of an enabled AI Model whose account is enabled; yield nothing otherwise."""
	model = frappe.get_doc("AI Model", ai_model)
	if not model.enabled:
		return
	account = frappe.get_doc("AI Provider Account", model.account)
	if account.enabled:
		yield account, model


def bounded_options(options: dict, deadline: float | None) -> dict:
	"""Return `options` with a request timeout that ends by `deadline`; raise DeadlineReached after it."""
	if deadline is None:
		return options
	remaining = deadline - time.monotonic()
	if remaining <= 0:
		raise DeadlineReached(frappe._("The AI time budget is used up"))
	return {**options, "timeout": min(options["timeout"], remaining)}


def try_model(account, model: str, build, options: dict, context: dict, deadline: float | None = None) -> dict:
	"""Run one model request with retries; `build(adapter, credentials, model)` returns (url, headers, body)."""
	for attempt in range(1, options["attempts"] + 1):
		outcome = _attempt(account, model, build, bounded_options(options, deadline), context, deadline)
		if outcome["ok"]:
			return outcome
		if deadline is not None and time.monotonic() >= deadline:
			raise DeadlineReached(frappe._("The AI time budget ended during the request"))
		if not outcome["retryable"]:
			return outcome
		if attempt < options["attempts"]:
			time.sleep(min(2 * attempt, 8))
	return outcome


def new_state() -> dict:
	"""Return the record of one request attempt that `execute` and `record_failure` fill."""
	return dict(
		body={},
		result={},
		usage={},
		raw_text="",
		data=None,
		finish_reason=None,
		http_status=None,
		status="Error",
		error=frappe._("AI attempt did not complete"),
		retryable=False,
		secrets=[],
		calls=[],
	)


def _attempt(account, model, build, options, context, deadline=None):
	started = time.monotonic()
	state = new_state()
	provider = account.get("provider")
	qualified_model = f"{provider}/{model}"
	try:
		execute(account, model, build, options, state, deadline)
		state.update(status="Success", error=None)
	except JobTimeoutException:
		state.update(status="Timeout", error=frappe._("AI job timed out"))
		raise
	except DeadlineReached as exc:
		state.update(status="Timeout", error=str(exc))
		raise
	except Exception as exc:
		record_failure(state, exc)
	finally:
		latency = int((time.monotonic() - started) * 1000)
		_log_attempt(state, qualified_model, provider, latency, context)
	if state["status"] == "Success":
		return {
			"ok": True,
			"result": {
				"data": state["data"],
				"usage": state["usage"],
				"model": qualified_model,
				"latency_ms": latency,
				"finish_reason": state["finish_reason"],
				"tool_calls": state["calls"],
				"response": state["result"],
			},
		}
	return {"ok": False, "error": state["error"], "retryable": state["retryable"]}


def execute(account, model: str, build, options: dict, state: dict, deadline: float | None = None) -> None:
	"""Send one request and fill `state`; raise on failure.

	Each step gets only the time left before `deadline`; the answer is read in parts and stops at it.
	A reply that is not JSON fails unless `options["require_json"]` is false.
	"""
	credentials = accounts.credentials(account, bounded_options(options, deadline)["timeout"])
	state["secrets"] = [credentials.get(field) for field in ("api_key", "access_token", "refresh_token")]
	adapter = PROVIDERS[account.get("provider")]
	url, headers, state["body"] = build(adapter, credentials, model)
	stream = adapter is codex
	response = requests.post(
		url, headers=headers, json=state["body"], timeout=bounded_options(options, deadline)["timeout"], stream=True
	)
	try:
		state["http_status"] = response.status_code
		if not stream:
			response._content = read_body(response, deadline)
		if response.status_code >= 400:
			try:
				state["result"] = response.json()
			except ValueError:
				state["result"] = {"non_json_response": llm.redact_secrets(response.text or "", state["secrets"])[:4000]}
			raise ProviderError(
				error_message(state["result"]) or frappe._("Provider returned HTTP {0}").format(response.status_code),
				response.status_code,
			)
		parsed = codex.parse_response(response, deadline) if stream else adapter.parse_response(response)
		state["raw_text"], state["usage"], state["finish_reason"], state["result"] = parsed
		if deadline is not None and time.monotonic() >= deadline:
			raise requests.Timeout(frappe._("The AI answer arrived after the time budget ended"))
		state["calls"] = adapter.tool_calls(state["result"]) if state["body"].get("tools") else []
		state["data"] = llm.safe_json_loads(state["raw_text"])
		if state["data"] is None and not state["calls"] and options.get("require_json", True):
			raise ValueError(_json_error(state))
	finally:
		response.close()


def read_body(response: requests.Response, deadline: float | None) -> bytes:
	"""Read the whole body; raise Timeout when `deadline` passes, so a slowly sent answer cannot run past it."""
	chunks = []
	for chunk in response.iter_content(BODY_CHUNK_BYTES):
		if deadline is not None and time.monotonic() >= deadline:
			raise requests.Timeout(frappe._("The AI time budget ended while the answer was read"))
		chunks.append(chunk)
	return b"".join(chunks)


def record_failure(state: dict, exc: Exception) -> None:
	"""Record the status, redacted error and retry decision of a failed attempt in `state`."""
	if isinstance(exc, ProviderError):
		if exc.result is not None:
			state["result"] = exc.result
		state["raw_text"] = exc.raw_text
		state["finish_reason"] = exc.finish_reason
		state["usage"] = exc.usage or state["usage"]
	code = getattr(exc, "status_code", None) or state["http_status"]
	state["http_status"] = code
	state["status"] = "Timeout" if isinstance(exc, requests.Timeout) else "Error"
	state["error"] = llm.redact_secrets(str(exc), state["secrets"])
	if isinstance(exc, ProviderError):
		state["error"] = state["error"][:500]
		if exc.finish_reason == "incomplete":
			state["error"] += ". " + _json_error(state)
	state["retryable"] = _is_retryable(exc, code, state["finish_reason"])


def _is_retryable(exc, code, finish_reason):
	"""Retry network and server errors, and unparsable answers; not answers that ran out or failed."""
	if isinstance(exc, requests.RequestException) or code in (408, 429) or (code or 0) >= 500:
		return True
	return bool(code) and code < 400 and finish_reason not in ("incomplete", "failed")


def _json_error(state):
	hint = ""
	if state["finish_reason"] in ("length", "max_tokens", "incomplete"):
		hint = frappe._(" — response was truncated; raise 'Max Tokens' on the AI Model")
	snippet = llm.redact_secrets(state["raw_text"], state["secrets"])[:200] or frappe._("<empty response>")
	return frappe._("could not parse JSON from model response{0}. Raw begins: {1}").format(hint, snippet)


def _log_attempt(state, model, provider, latency, context):
	llm.log_call(
		model=model,
		provider=provider,
		status=state["status"],
		http_status=state["http_status"],
		latency_ms=latency,
		usage=state["usage"],
		body=state["body"],
		result=state["result"],
		error=state["error"],
		raw_text=state["raw_text"],
		finish_reason=state["finish_reason"],
		secrets=state["secrets"],
		**context,
	)

"""Provider-neutral transport for subscription-authenticated local agents.

The document runtime owns tools and database writes.  A local agent receives a
bounded transcript and can only *request* one of those tools or return a final
Decision.  This keeps Codex/other agent harnesses useful without granting them
direct Frappe mutation access.
"""

from __future__ import annotations

import fcntl
import base64
import binascii
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint

from frappe_tools.i2a import providers as api_providers
from frappe_tools.utils.llm import safe_json_loads
from frappe_tools.automation.codex_app_server import (
	AppServerError,
	CodexAppServerClient,
	dynamic_tool_specs,
)


SUBSCRIPTION_PROVIDERS = {"Codex OAuth", "Claude OAuth"}
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_QUEUE_TIMEOUT_SECONDS = 30
DEFAULT_MAX_CONCURRENCY = 2
MAX_AGENT_INPUT_CHARS = 240_000
MAX_VISION_IMAGES = 24
MAX_VISION_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VISION_TOTAL_BYTES = 120 * 1024 * 1024

_VISION_SCHEMA = {
	"type": "object",
	"properties": {"json": {"type": "string"}},
	"required": ["json"],
	"additionalProperties": False,
}

_VISION_TYPES = {
	"image/jpeg": ".jpg",
	"image/png": ".png",
	"image/webp": ".webp",
}

_TURN_SCHEMA = {
	"type": "object",
	"properties": {
		"kind": {"type": "string", "enum": ["tool_calls", "decision"]},
		"tool_calls": {
			"type": "array",
			"items": {
				"type": "object",
				"properties": {
					"id": {"type": "string"},
					"name": {"type": "string"},
					"arguments_json": {"type": "string"},
				},
				"required": ["id", "name", "arguments_json"],
				"additionalProperties": False,
			},
		},
		"decision": {
			"type": "object",
			"properties": {
				"status": {"type": "string", "enum": ["review", "handoff"]},
				"confidence": {"type": "number", "minimum": 0, "maximum": 1},
				"values_json": {"type": "string"},
				"reasons": {"type": "array", "items": {"type": "string"}},
				"handoff_reasons": {"type": "array", "items": {"type": "string"}},
				"memory_sources": {"type": "array", "items": {"type": "string"}},
			},
			"required": [
				"status", "confidence", "values_json", "reasons", "handoff_reasons", "memory_sources",
			],
			"additionalProperties": False,
		},
	},
	"required": ["kind", "tool_calls", "decision"],
	"additionalProperties": False,
}


class SubscriptionAgent(ABC):
	"""Stable extension point for local subscription-backed agent harnesses."""

	@abstractmethod
	def call_with_tools(self, ai_model, messages, tool_specs, *, purpose="", run=None, action=None, max_tokens=None):
		raise NotImplementedError

	@abstractmethod
	def health(self, ai_model):
		raise NotImplementedError

	def call_with_live_tools(
		self, ai_model, messages, tool_specs, tool_handler, *, purpose="", run=None, action=None, max_tokens=None
	):
		"""Optional single-turn host-tool loop implemented by app-server transports."""
		raise NotImplementedError


class CodexOAuthAgent(SubscriptionAgent):
	"""Run Codex App Server with Codex-managed ChatGPT OAuth."""

	def call_with_tools(self, ai_model, messages, tool_specs, *, purpose="", run=None, action=None, max_tokens=None):
		return self._call(
			ai_model,
			_turn_prompt(messages, tool_specs),
			[],
			None,
			purpose=purpose,
			run=run,
			action=action,
			message_count=len(messages),
			tool_specs=tool_specs,
		)

	def call_with_live_tools(
		self, ai_model, messages, tool_specs, tool_handler, *, purpose="", run=None, action=None, max_tokens=None
	):
		return self._call(
			ai_model,
			_live_tool_prompt(messages, tool_specs),
			dynamic_tool_specs(tool_specs),
			tool_handler,
			purpose=purpose,
			run=run,
			action=action,
			message_count=len(messages),
			tool_specs=tool_specs,
		)

	def call_vision(self, ai_model, image_data_urls, system_prompt, user_prompt, *, run=None, action=None):
		"""Extract one schema-constrained JSON payload from local document images.

		Images are materialized only inside the site's private agent runtime and
		passed to Codex App Server as native local-image inputs. The directory is
		removed after the turn, and no image bytes or paths enter the audit log.
		"""
		if not cint(ai_model.enabled):
			raise api_providers.ProviderError(_("AI Model {0} is disabled").format(ai_model.name))
		if ai_model.provider != "Codex OAuth":
			raise api_providers.ProviderError(_("Document vision requires a Codex OAuth AI Model"))
		if not cint(_get(ai_model, "supports_vision")):
			raise api_providers.ProviderError(_("AI Model {0} is not enabled for vision").format(ai_model.name))
		if _get(ai_model, "fallback_model"):
			raise api_providers.ProviderError(_("Document vision does not permit fallback models"))

		urls = list(image_data_urls or [])
		if not urls:
			raise api_providers.ProviderError(_("Document vision needs at least one image"))
		if len(urls) > MAX_VISION_IMAGES:
			raise api_providers.ProviderError(
				_("Document vision accepts at most {0} images per turn").format(MAX_VISION_IMAGES)
			)

		prompt = _vision_prompt(system_prompt, user_prompt)
		if len(prompt) > MAX_AGENT_INPUT_CHARS:
			raise api_providers.ProviderError(_("Document vision prompt exceeds the safe input limit"))

		executable = _resolve_executable(ai_model, "codex")
		root = _work_root()
		timeout = max(cint(_get(ai_model, "agent_timeout_seconds")) or DEFAULT_TIMEOUT_SECONDS, 30)
		started = time.monotonic()
		result, usage, error, status = {}, {}, None, "Error"

		try:
			with _agent_slot(ai_model, root):
				with tempfile.TemporaryDirectory(prefix="codex-vision-", dir=root) as run_dir:
					image_paths = _materialize_vision_images(urls, run_dir)
					input_items = [{"type": "text", "text": prompt}]
					input_items.extend({"type": "localImage", "path": path} for path in image_paths)
					with CodexAppServerClient(
						executable,
						cwd=run_dir,
						environment=_safe_environment(run_dir),
						timeout=timeout,
					) as client:
						app_result = client.run_structured(
							prompt=prompt,
							input_items=input_items,
							output_schema=_VISION_SCHEMA,
							model=_get(ai_model, "model_id"),
							dynamic_tools=[],
						)
					usage = app_result.get("usage") or {}
					envelope = safe_json_loads(app_result.get("text") or "")
					if not isinstance(envelope, dict) or not isinstance(envelope.get("json"), str):
						raise api_providers.ProviderError(_("Codex returned an invalid vision envelope"))
					result = safe_json_loads(envelope["json"])
					for decode_attempt in range(2):
						if not isinstance(result, str):
							break
						result = safe_json_loads(result)
					if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
						result = result[0]
					if not isinstance(result, dict):
						invalid_type = type(result).__name__
						# Keep bounded diagnostics in the protected document audit, not
						# in the user-facing exception or browser response.
						result = {"invalid_json_preview": envelope["json"][:8000]}
						raise api_providers.ProviderError(
							_("Codex returned invalid document JSON ({0}, {1} characters)").format(
								invalid_type, len(envelope["json"])
							)
						)
					status = "Success"
					return {
						"data": result,
						"usage": usage,
						"model": _get(ai_model, "model_id") or ai_model.name,
						"latency_ms": int((time.monotonic() - started) * 1000),
					}
		except (subprocess.TimeoutExpired, TimeoutError):
			status = "Timeout"
			error = _("Codex vision exceeded the {0}s timeout").format(timeout)
			raise api_providers.ProviderError(error)
		except AppServerError as exc:
			error = _redact_error(str(exc))
			raise api_providers.ProviderError(_codex_error(error, "")) from exc
		except api_providers.ProviderError as exc:
			error = str(exc)
			raise
		except (OSError, ValueError, binascii.Error) as exc:
			error = str(exc)
			raise api_providers.ProviderError(_("Could not run Codex document vision: {0}").format(exc)) from exc
		finally:
			_log_agent_call(
				ai_model=ai_model,
				purpose="document_vision",
				reference_name=run,
				adapter_id=action,
				status=status,
				http_status=None,
				latency_ms=int((time.monotonic() - started) * 1000),
				usage=usage,
				cost=0,
				cost_estimated=0,
				body={"transport": "codex_app_server", "image_count": len(urls)},
				result=result,
				error=error,
			)

	def _call(
		self, ai_model, prompt, live_tools, tool_handler, *, purpose, run, action, message_count, tool_specs
	):
		if not cint(ai_model.enabled):
			raise api_providers.ProviderError(_("AI Model {0} is disabled").format(ai_model.name))

		if len(prompt) > MAX_AGENT_INPUT_CHARS:
			raise api_providers.ProviderError(
				_("Agent input is {0} characters; the safe limit is {1}. Reduce adapter references.").format(
					len(prompt), MAX_AGENT_INPUT_CHARS
				)
			)

		executable = _resolve_executable(ai_model, "codex")
		root = _work_root()
		timeout = max(cint(_get(ai_model, "agent_timeout_seconds")) or DEFAULT_TIMEOUT_SECONDS, 30)
		started = time.monotonic()
		result, usage, error, status = {}, {}, None, "Error"

		try:
			with _agent_slot(ai_model, root):
				with tempfile.TemporaryDirectory(prefix="codex-", dir=root) as run_dir:
					with CodexAppServerClient(
						executable,
						cwd=run_dir,
						environment=_safe_environment(run_dir),
						timeout=timeout,
					) as client:
						app_result = client.run_structured(
							prompt=prompt,
							output_schema=_TURN_SCHEMA,
							model=_get(ai_model, "model_id"),
							dynamic_tools=live_tools,
							tool_handler=tool_handler,
						)
					usage = app_result.get("usage") or {}
					result = safe_json_loads(app_result.get("text") or "")
					if not isinstance(result, dict):
						raise api_providers.ProviderError(_("Codex returned an invalid structured response"))
					response = _normalize_turn(result)
					response["tool_trace"] = app_result.get("tool_trace") or []
					response["agent_thread_id"] = app_result.get("thread_id")
					status = "Success"
					return {
						**response,
						"transport_provider": ai_model.provider,
						"transport_model": ai_model.name,
						"usage": usage,
					}
		except (subprocess.TimeoutExpired, TimeoutError):
			status = "Timeout"
			error = _("Codex agent exceeded the {0}s timeout").format(timeout)
			raise api_providers.ProviderError(error)
		except AppServerError as exc:
			error = _redact_error(str(exc))
			raise api_providers.ProviderError(_codex_error(error, "")) from exc
		except api_providers.ProviderError as exc:
			error = str(exc)
			raise
		except OSError as exc:
			error = str(exc)
			raise api_providers.ProviderError(_("Could not start Codex agent: {0}").format(exc)) from exc
		finally:
			_log_agent_call(
				ai_model=ai_model,
				purpose=purpose,
				reference_name=run,
				adapter_id=action,
				status=status,
				http_status=None,
				latency_ms=int((time.monotonic() - started) * 1000),
				usage=usage,
				cost=0,
				cost_estimated=0,
				body={"transport": "codex_app_server", "message_count": message_count, "tool_names": [
					(_get(spec, "function") or spec).get("name") for spec in tool_specs
				], "live_tools": bool(live_tools)},
				result=result,
				error=error,
			)

	def health(self, ai_model):
		try:
			executable = _resolve_executable(ai_model, "codex")
		except api_providers.ProviderError as exc:
			return {"ready": False, "provider": "Codex OAuth", "reason": str(exc)}
		root = _work_root()
		try:
			with tempfile.TemporaryDirectory(prefix="codex-health-", dir=root) as run_dir:
				with CodexAppServerClient(
					executable, cwd=run_dir, environment=_safe_environment(run_dir), timeout=15
				) as client:
					account = client.account()
		except (OSError, subprocess.TimeoutExpired, TimeoutError, AppServerError) as exc:
			return {"ready": False, "provider": "Codex OAuth", "reason": str(exc)[:300]}
		identity = account.get("account") or {}
		ready = identity.get("type") == "chatgpt"
		return {
			"ready": ready,
			"provider": "Codex OAuth",
			"model": _get(ai_model, "model_id"),
			"executable": executable,
			"transport": "codex_app_server",
			"account_type": identity.get("type"),
			"plan_type": identity.get("planType"),
			"reason": None if ready else "Codex App Server ChatGPT OAuth login is not ready",
		}


_BUILTIN_AGENTS = {"Codex OAuth": CodexOAuthAgent}


def call_with_tools(
	ai_model,
	messages,
	tool_specs,
	*,
	purpose="",
	run=None,
	action=None,
	max_tokens=None,
	tool_choice="auto",
):
	"""Call a configured agent and walk its bounded fallback chain."""
	if isinstance(ai_model, str):
		ai_model = frappe.get_doc("AI Model", ai_model)
	last_error = None
	visited = set()
	current = ai_model
	while current and current.name not in visited:
		visited.add(current.name)
		try:
			if current.provider in SUBSCRIPTION_PROVIDERS:
				return _agent_for(current.provider).call_with_tools(
					current, messages, tool_specs, purpose=purpose, run=run, action=action, max_tokens=max_tokens
				)
			response = api_providers.call_with_tools(
				current, messages, tool_specs, purpose=purpose, run=run, action=action,
				max_tokens=max_tokens, tool_choice=tool_choice,
			)
			response.setdefault("transport_provider", current.provider)
			response.setdefault("transport_model", current.name)
			return response
		except api_providers.ProviderError as exc:
			last_error = exc
			fallback = _get(current, "fallback_model")
			if not fallback:
				break
			current = frappe.get_doc("AI Model", fallback)
	if current and current.name in visited and _get(current, "fallback_model"):
		raise api_providers.ProviderError(_("AI Model fallback cycle detected"))
	raise api_providers.ProviderError(str(last_error or _("No agent provider is configured")))


def supports_live_tools(model_name):
	"""Return whether a model can keep host tool calls inside one native agent turn."""
	ai_model = frappe.get_doc("AI Model", model_name) if isinstance(model_name, str) else model_name
	provider = _get(ai_model, "provider")
	if provider not in SUBSCRIPTION_PROVIDERS:
		return False
	agent = _agent_for(provider)
	return type(agent).call_with_live_tools is not SubscriptionAgent.call_with_live_tools


def call_with_live_tools(
	model_name,
	messages,
	tool_specs,
	tool_handler,
	*,
	purpose="",
	run=None,
	action=None,
	max_tokens=None,
):
	"""Run one subscription-agent turn whose only callable tools are host-owned."""
	ai_model = frappe.get_doc("AI Model", model_name) if isinstance(model_name, str) else model_name
	if not supports_live_tools(ai_model):
		raise api_providers.ProviderError(_("AI Model {0} does not support live host tools").format(ai_model.name))
	return _agent_for(ai_model.provider).call_with_live_tools(
		ai_model,
		messages,
		tool_specs,
		tool_handler,
		purpose=purpose,
		run=run,
		action=action,
		max_tokens=max_tokens,
	)


def call_vision(model_name, image_data_urls, system_prompt, user_prompt, *, run=None, action=None):
	"""Run the Codex-only document vision boundary with no fallback chain."""
	ai_model = frappe.get_doc("AI Model", model_name) if isinstance(model_name, str) else model_name
	if ai_model.provider != "Codex OAuth":
		raise api_providers.ProviderError(_("Document vision requires a Codex OAuth AI Model"))
	if _get(ai_model, "fallback_model"):
		raise api_providers.ProviderError(_("Document vision does not permit fallback models"))
	return CodexOAuthAgent().call_vision(
		ai_model,
		image_data_urls,
		system_prompt,
		user_prompt,
		run=run,
		action=action,
	)


def health(model_name):
	ai_model = frappe.get_doc("AI Model", model_name) if isinstance(model_name, str) else model_name
	if ai_model.provider in SUBSCRIPTION_PROVIDERS:
		return _agent_for(ai_model.provider).health(ai_model)
	credential = ai_model.get_password("api_key", raise_exception=False)
	ready = bool(cint(ai_model.enabled) and credential)
	if not cint(ai_model.enabled):
		reason = _("AI Model is disabled")
	elif not credential:
		reason = _("API credential is not configured")
	else:
		reason = None
	return {
		"ready": ready,
		"provider": ai_model.provider,
		"model": ai_model.model_id,
		"reason": reason,
	}


@frappe.whitelist()
def get_agent_health(model_name):
	frappe.only_for("System Manager")
	return health(model_name)


@frappe.whitelist()
def run_agent_smoke_test(model_name):
	"""Exercise the real transport with synthetic data and no Frappe read tools."""
	frappe.only_for("System Manager")
	return call_with_tools(
		model_name,
		[
			{"role": "system", "content": "Return a safe document decision from only the supplied facts."},
			{"role": "user", "content": "Synthetic facts: document_type=LR; invoice_reference=INV-TEST-001; local_match=INV-TEST-001."},
		],
		[],
		purpose="agent_health_check",
	)


@frappe.whitelist()
def run_agent_live_tool_smoke_test(model_name):
	"""Prove the app-server can call one bounded host tool and use its result."""
	frappe.only_for("System Manager")
	return call_with_live_tools(
		model_name,
		[
			{"role": "system", "content": "Use the local lookup tool before deciding."},
			{"role": "user", "content": "Resolve synthetic code ABC and put its returned value in Decision.values."},
		],
		[{
			"type": "function",
			"function": {
				"name": "local_lookup",
				"description": "Return one synthetic local reference for an integration health check.",
				"parameters": {
					"type": "object",
					"properties": {"code": {"type": "string"}},
					"required": ["code"],
					"additionalProperties": False,
				},
			},
		}],
		lambda name, arguments: {
			"code": arguments.get("code"),
			"value": "LOCAL-42" if name == "local_lookup" and arguments.get("code") == "ABC" else None,
		},
		purpose="agent_live_tool_health_check",
	)


def _agent_for(provider):
	provider_class = _BUILTIN_AGENTS.get(provider)
	if not provider_class:
		configured = frappe.get_hooks("document_subscription_agent_providers") or {}
		if isinstance(configured, list):
			merged = {}
			for value in configured:
				if isinstance(value, dict):
					merged.update(value)
			configured = merged
		dotted_path = configured.get(provider) if isinstance(configured, dict) else None
		if dotted_path:
			provider_class = frappe.get_attr(dotted_path)
	if not provider_class:
		raise api_providers.ProviderError(_("Subscription agent provider {0} is not registered").format(provider))
	return provider_class()


def _log_agent_call(*, ai_model, purpose, reference_name, adapter_id, status, http_status, latency_ms, usage,
		cost, cost_estimated, body, result, error):
	"""Persist one safe audit row per local subscription-agent process."""
	try:
		log = frappe.new_doc("Document Agent Call")
		log.reference_doctype = "Document Extraction" if reference_name else None
		log.reference_name = reference_name
		log.adapter_id = adapter_id
		log.ai_model = ai_model.name
		log.provider = ai_model.provider
		log.purpose = purpose
		log.status = status
		log.http_status = http_status
		log.latency_ms = latency_ms
		log.prompt_tokens = cint((usage or {}).get("prompt_tokens"))
		log.completion_tokens = cint((usage or {}).get("completion_tokens"))
		log.cached_tokens = cint((usage or {}).get("cached_tokens"))
		log.total_tokens = cint((usage or {}).get("total_tokens"))
		log.cost_usd = cost
		log.cost_estimated = cost_estimated
		log.request_summary = frappe.as_json(body)[:140000]
		log.response_payload = frappe.as_json(result)[:140000]
		log.error_message = (error or "")[:1000]
		log.flags.ignore_permissions = True
		log.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Document Agent Call insert failed")


def _turn_prompt(messages, tool_specs):
	return """You are the reasoning worker inside a document automation product.
You have no authority to mutate data. Do not run shell commands, inspect the filesystem, browse the web, or call external tools.
Use only the supplied transcript and declared read-tool schemas. Return exactly the schema-constrained object requested by the caller.

If more local evidence is required, set kind=\"tool_calls\" and request one or more
declared tools. Put every tool argument object in arguments_json as valid JSON.
Do not request undeclared tools.

When evidence is sufficient, set kind=\"decision\". Put the complete Decision.values
object in values_json as valid JSON. status must be \"review\" only when the evidence
supports the result; otherwise use \"handoff\" and explain the blockers. Never invent
Frappe identifiers or reference records.

For tool_calls responses, fill decision with status=\"handoff\", confidence=0,
values_json=\"{}\", and empty reason/source arrays; it will be ignored.

Conversation transcript:
""" + json.dumps(messages, default=str, ensure_ascii=False) + "\n\nDeclared read tools:\n" + json.dumps(
		tool_specs, default=str, ensure_ascii=False
	)


def _vision_prompt(system_prompt, user_prompt):
	return """You are the vision extraction worker inside a Frappe document automation product.
Read only the document images attached to this turn. Do not use shell commands, filesystem tools,
web search, applications, skills, MCP servers, or prior knowledge to invent business values.
Follow the supplied extraction instructions exactly. Preserve the requested normalized bounding-box
coordinate system and return every value with its visible evidence. If text is unreadable, return
null/low confidence instead of guessing.

The output schema contains one string field named json. Put the complete requested JSON object into
that string as valid JSON. Do not add Markdown or commentary outside the schema.

Extraction policy:
""" + str(system_prompt or "") + "\n\nDocument task:\n" + str(user_prompt or "")


def _materialize_vision_images(image_data_urls, run_dir):
	paths = []
	total_bytes = 0
	for index, data_url in enumerate(image_data_urls, 1):
		match = re.fullmatch(r"data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\r\n]+)", str(data_url or ""))
		if not match:
			raise ValueError(_("Only PNG, JPEG, or WebP document images are accepted"))
		mime_type, payload = match.groups()
		try:
			content = base64.b64decode(payload, validate=True)
		except (ValueError, binascii.Error) as exc:
			raise ValueError(_("Document image {0} has invalid base64 data").format(index)) from exc
		if not content or len(content) > MAX_VISION_IMAGE_BYTES:
			raise ValueError(
				_("Document image {0} exceeds the {1} MB limit").format(index, MAX_VISION_IMAGE_BYTES // 1024 // 1024)
			)
		total_bytes += len(content)
		if total_bytes > MAX_VISION_TOTAL_BYTES:
			raise ValueError(_("Document images exceed the total vision input limit"))
		path = Path(run_dir, f"page-{index:03d}{_VISION_TYPES[mime_type]}").resolve()
		path.write_bytes(content)
		path.chmod(0o600)
		paths.append(str(path))
	return paths


def _live_tool_prompt(messages, tool_specs):
	return """You are the reasoning worker inside a document automation product.
You have no authority to mutate data. Do not run shell commands, inspect the filesystem, browse the web, use apps, or call undeclared tools.
Use the host-owned dynamic read tools whenever more local evidence is required. Tool responses are local application facts. Do not describe a tool call in the final answer; actually call it.

After all necessary tool calls complete, return exactly the schema-constrained object requested by the caller with kind=\"decision\" and tool_calls=[]. Put the complete Decision.values object in values_json as valid JSON. status must be \"review\" only when the evidence supports the result; otherwise use \"handoff\" and explain the blockers. Never invent Frappe identifiers or reference records.

Conversation transcript:
""" + json.dumps(messages, default=str, ensure_ascii=False) + "\n\nAvailable host-owned read tools:\n" + json.dumps(
		tool_specs, default=str, ensure_ascii=False
	)


def _normalize_turn(result):
	kind = result.get("kind")
	if kind == "tool_calls":
		calls = []
		for index, item in enumerate(result.get("tool_calls") or [], 1):
			arguments = safe_json_loads(item.get("arguments_json") or "{}")
			if not isinstance(arguments, dict):
				arguments = {"__unparseable_arguments__": str(item.get("arguments_json") or "")[:120]}
			calls.append({
				"id": item.get("id") or f"agent-tool-{index}",
				"name": item.get("name"),
				"arguments": arguments,
			})
		if not calls:
			raise api_providers.ProviderError(_("Agent selected tool_calls but requested no tools"))
		return {
			"content": "",
			"tool_calls": calls,
			"finish_reason": "tool_calls",
			"message": {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
		}
	if kind != "decision":
		raise api_providers.ProviderError(_("Agent response kind must be tool_calls or decision"))
	decision = result.get("decision") or {}
	values = safe_json_loads(decision.get("values_json") or "{}")
	if not isinstance(values, dict):
		raise api_providers.ProviderError(_("Agent decision values_json must contain an object"))
	content = json.dumps({
		"status": decision.get("status"),
		"confidence": decision.get("confidence"),
		"values": values,
		"reasons": decision.get("reasons") or [],
		"handoff_reasons": decision.get("handoff_reasons") or [],
		"memory_sources": decision.get("memory_sources") or [],
	}, ensure_ascii=False)
	return {
		"content": content,
		"tool_calls": [],
		"finish_reason": "decision",
		"message": {"role": "assistant", "content": content},
	}


def _usage_from_jsonl(output):
	usage = {}
	for line in (output or "").splitlines():
		try:
			event = json.loads(line)
		except (TypeError, ValueError):
			continue
		if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
			usage = event["usage"]
	input_tokens = cint(usage.get("input_tokens"))
	output_tokens = cint(usage.get("output_tokens"))
	return {
		"prompt_tokens": input_tokens,
		"completion_tokens": output_tokens,
		"total_tokens": input_tokens + output_tokens,
		"cached_tokens": cint(usage.get("cached_input_tokens")),
	}


def _resolve_executable(ai_model, default):
	configured = str(_get(ai_model, "agent_executable") or "").strip()
	if configured:
		path = Path(configured).expanduser()
		if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
			raise api_providers.ProviderError(_("Configured agent executable is not an executable absolute path"))
		return str(path)
	if default == "codex":
		# Product deployments install the official runtime as this app's optional
		# dependency, exactly like OpenClaw's managed Codex plugin. This keeps a
		# separate global Codex CLI installation optional.
		managed = Path(__file__).resolve().parents[2] / "node_modules" / ".bin" / "codex"
		if managed.is_file() and os.access(managed, os.X_OK):
			return str(managed)
	# C3 deliberately places a launcher shim at `codex`; background workers do
	# not have the launcher protocol, so use its disclosed real binary directly.
	launcher_real = str(os.environ.get("C3_CODEX_REAL") or "").strip() if default == "codex" else ""
	if launcher_real:
		candidate = Path(launcher_real).expanduser()
		if candidate.is_absolute() and candidate.is_file() and os.access(candidate, os.X_OK):
			return str(candidate)
	path = shutil.which(default)
	if not path:
		raise api_providers.ProviderError(_("{0} executable is not available to the Frappe worker").format(default))
	return path


def _work_root():
	configured = str(getattr(frappe, "conf", {}).get("document_agent_work_dir") or "").strip()
	root = Path(configured).expanduser() if configured else Path(frappe.get_site_path("private", "agent-runtime"))
	root.mkdir(parents=True, exist_ok=True, mode=0o700)
	try:
		root.chmod(0o700)
	except OSError:
		pass
	return str(root)


@contextmanager
def _agent_slot(ai_model, root):
	maximum = max(1, min(cint(_get(ai_model, "agent_max_concurrency")) or DEFAULT_MAX_CONCURRENCY, 16))
	wait_seconds = max(0, cint(_get(ai_model, "agent_queue_timeout_seconds")) or DEFAULT_QUEUE_TIMEOUT_SECONDS)
	deadline = time.monotonic() + wait_seconds
	lock_handle = None
	lock_dir = Path(root) / "locks"
	lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
	name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(ai_model.name))[:100]
	while lock_handle is None:
		for slot in range(maximum):
			handle = (lock_dir / f"{name}.{slot}.lock").open("a+")
			try:
				fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
				lock_handle = handle
				break
			except BlockingIOError:
				handle.close()
		if lock_handle is not None:
			break
		if time.monotonic() >= deadline:
			raise api_providers.ProviderError(
				_("All {0} agent slots are busy; retry this queued job later").format(maximum)
			)
		time.sleep(0.2)
	try:
		yield
	finally:
		fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
		lock_handle.close()


def _safe_environment(temp_dir):
	allowed = {
		"HOME", "PATH", "CODEX_HOME", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
		"HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
	}
	environment = {key: value for key, value in os.environ.items() if key in allowed}
	environment["TMPDIR"] = str(temp_dir)
	# Never let a stray API key silently replace the intended ChatGPT OAuth login.
	environment.pop("OPENAI_API_KEY", None)
	return environment


def _codex_error(stderr, stdout):
	text = "\n".join(filter(None, [(stderr or "").strip(), (stdout or "").strip()]))
	clean = _redact_error(text)
	lowered = clean.casefold()
	if "login" in lowered or "authentication" in lowered or "unauthorized" in lowered:
		return _("Codex OAuth authentication is required; run `codex login` as the Frappe service user")
	if "usage limit" in lowered or "rate limit" in lowered or "quota" in lowered:
		return _("Codex subscription limit was reached; use the configured fallback model or retry later")
	return clean[-800:] or _("Codex agent process failed")


def _redact_error(text):
	text = re.sub(r"(?i)(access_token|refresh_token|authorization|api[_ -]?key)(\s*[:=]\s*)\S+", r"\1\2<redacted>", str(text or ""))
	text = re.sub(r"\b(sk-[A-Za-z0-9_-]{8,})\b", "<redacted>", text)
	return text[:1200]


def _get(value: Any, key: str, default=None):
	getter = getattr(value, "get", None)
	if not getter:
		return getattr(value, key, default)
	result = getter(key)
	return default if result is None else result

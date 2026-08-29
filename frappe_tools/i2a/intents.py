"""Bounded text/multimodal intent execution using an ``I2A Action``.

The document-oriented I2A engine owns extraction and reconciliation flows.
Conversation products also need the same configured models, budgets, audit log,
and prompts without pretending that a chat transcript is a scanned document.
This module is that small reusable boundary: callers supply text and optional
OpenRouter-compatible content parts; Frappe Tools retains model credentials and
logs every provider attempt through :mod:`frappe_tools.i2a.providers`.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import cint

from frappe_tools.i2a import providers


def run_intent(
	action_name: str,
	text: str,
	*,
	content_parts: list[dict] | None = None,
	max_tokens: int | None = None,
) -> dict:
	"""Run one configured I2A Action as a JSON intent/summarization task.

	``content_parts`` may contain image, audio, or file parts supported by the
	configured provider. They are never persisted by this module; provider audit
	logs redact their base64 payloads before insertion.
	"""
	action_name = str(action_name or "").strip()
	if not action_name or not frappe.db.exists("I2A Action", action_name):
		raise providers.ProviderError("A valid I2A Action is required")
	action = frappe.get_doc("I2A Action", action_name)
	if not cint(action.enabled):
		raise providers.ProviderError(f"I2A Action {action_name} is disabled")

	orchestrators = [row for row in action.models if cint(row.is_orchestrator)]
	if len(orchestrators) != 1:
		raise providers.ProviderError(
			f"I2A Action {action_name} must have exactly one orchestrator model"
		)

	system_sections = [
		str(action.instructions or "").strip(),
		str(action.knowledge or "").strip(),
		str(action.rules or "").strip(),
	]
	schema = action.parsed_schema()
	if schema:
		system_sections.append(
			"Return a JSON object matching this output schema: "
			+ json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
		)
	system_sections.append(
		"Use only supplied evidence. Preserve uncertainty and disagreement; "
		"never invent a person, event, amount, intent, or requested action."
	)
	parts = [{"type": "text", "text": str(text or "")}]
	parts.extend(_safe_content_parts(content_parts or []))
	result = providers.call_model(
		orchestrators[0].ai_model,
		[
			{"role": "system", "content": "\n\n".join(filter(None, system_sections))},
			{"role": "user", "content": parts},
		],
		json_mode=True,
		purpose="extract",
		action=action.name,
		max_tokens=max_tokens,
	)
	return {
		"data": result.get("data") or {},
		"model": orchestrators[0].ai_model,
		"usage": result.get("usage") or {},
		"latency_ms": result.get("latency_ms") or 0,
	}


def _safe_content_parts(parts: list[dict]) -> list[dict]:
	allowed = {"image_url", "input_audio", "file", "video_url"}
	result = []
	for part in parts:
		if not isinstance(part, dict) or part.get("type") not in allowed:
			continue
		result.append(part)
	return result

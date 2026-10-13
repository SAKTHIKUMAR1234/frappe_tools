"""OpenRouter Decisions client for scanner layout and section choices."""

import json
import math
import time
from copy import deepcopy

import requests

from frappe_tools.utils import llm

URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
MAX_INPUT_TOKENS = 26000


def _estimated_tokens(state, questions):
	return len(json.dumps({"state": state, "questions": questions}, ensure_ascii=False)) / 3.5


def _trim_items(pages):
	for page in pages:
		items = page.get("items") or []
		if len(items) > 15:
			page["items_truncated"] = len(items) - 15
			page["items"] = items[:15]


def _drop_other_fields(pages):
	for page in pages:
		page.pop("other_fields", None)


def _shorten_addresses(pages):
	for page in pages:
		for party in (page.get("parties") or {}).values():
			if isinstance(party, dict) and isinstance(party.get("address"), str):
				party["address"] = party["address"][:200]


def _bounded_state(state, questions):
	state = deepcopy(state)
	for shrink in (_trim_items, _drop_other_fields, _shorten_addresses):
		if _estimated_tokens(state, questions) <= MAX_INPUT_TOKENS:
			return state
		shrink(state.get("pages", []))
	if _estimated_tokens(state, questions) > MAX_INPUT_TOKENS:
		raise ValueError("Decision request exceeds the 26,000-token budget after shrinking page data.")
	return state


def decide(
	state: dict, questions: dict, target_doctype: str, *,
	phase: str | None = None, reference_doctype: str | None = None,
	reference_name: str | None = None, run_id: str | None = None,
) -> dict:
	"""Request decisions, validate choices, and log the attempt."""
	body = {"model": MODEL, "state": _bounded_state(state, questions), "questions": questions}
	attempt = dict(started=time.monotonic(), status="Success", http_status=None, result={}, error=None)
	try:
		_request(body, attempt)
		return _validate_answers(attempt["result"].get("answers") or {}, questions)
	except (requests.RequestException, ValueError, TypeError, KeyError, RuntimeError) as exc:
		attempt.update(status="Timeout" if isinstance(exc, requests.Timeout) else "Error", error=str(exc))
		raise
	finally:
		_log_attempt(body, attempt, target_doctype, dict(phase=phase,
			reference_doctype=reference_doctype, reference_name=reference_name, run_id=run_id))


def _request(body, attempt):
	settings = llm.ensure_ready()
	response = requests.post(URL, headers={"Authorization": f"Bearer {settings.get_api_key()}"},
		json=body, timeout=60)
	attempt["http_status"] = response.status_code
	response.raise_for_status()
	attempt["result"] = response.json()
	if not isinstance(attempt["result"], dict):
		raise ValueError("Decision response must be an object")


def _validate_answers(answers, questions):
	for key, question in questions.items():
		answer = answers.get(key) or {}
		confidence = answer.get("confidence")
		if answer.get("type") != "choice" or answer.get("choice") not in question["criteria"]:
			raise ValueError(f"Invalid Jev answer for {key}")
		if type(confidence) not in (float, int) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
			raise ValueError(f"Invalid Jev answer for {key}")
	return {key: answers[key] for key in questions}


def _log_attempt(body, attempt, target_doctype, context):
	result = attempt["result"]
	usage = (result.get("usage") or {}) if isinstance(result, dict) else {}
	tokens = usage.get("input_tokens", 0)
	llm.log_call(None, target_doctype, MODEL, attempt["status"], attempt["http_status"],
		int((time.monotonic() - attempt["started"]) * 1000),
		{"prompt_tokens": tokens, "total_tokens": tokens, "cost": usage.get("cost", 0)},
		body, result, attempt["error"], **context)

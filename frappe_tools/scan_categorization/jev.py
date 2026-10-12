"""OpenRouter Decisions client for scanner layout and section choices."""

import json
import math
import time

import requests

from frappe_tools.utils import llm

URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
MAX_REQUEST_BYTES = 24000


def decide(state: dict, questions: dict, target_doctype: str) -> dict:
	"""Return validated choice answers and log every HTTP attempt."""
	body = {"model": MODEL, "state": state, "questions": questions}
	if len(json.dumps(body, ensure_ascii=False).encode()) > MAX_REQUEST_BYTES:
		raise ValueError("Decision request is too large; reduce pages or layout sections.")
	settings = llm.ensure_ready()
	started = time.monotonic()
	status, http_status, error = "Success", None, None
	result, usage = {}, {}
	try:
		response = requests.post(
			URL,
			headers={"Authorization": f"Bearer {settings.get_api_key()}"},
			json=body,
			timeout=60,
		)
		http_status = response.status_code
		response.raise_for_status()
		result = response.json()
		usage = result.get("usage") or {}
		answers = result.get("answers") or {}
		for key, question in questions.items():
			answer = answers.get(key) or {}
			confidence = answer.get("confidence")
			if (
				answer.get("type") != "choice"
				or answer.get("choice") not in question["criteria"]
				or isinstance(confidence, bool)
				or not isinstance(confidence, (float, int))
				or not math.isfinite(confidence)
				or not 0 <= confidence <= 1
			):
				raise ValueError(f"Invalid Jev answer for {key}")
		return {key: answers[key] for key in questions}
	except Exception as exc:
		status = "Timeout" if isinstance(exc, requests.Timeout) else "Error"
		error = str(exc)
		raise
	finally:
		tokens = usage.get("input_tokens", 0)
		llm._log_call(
			None, target_doctype, MODEL, status, http_status,
			int((time.monotonic() - started) * 1000),
			{"prompt_tokens": tokens, "total_tokens": tokens, "cost": usage.get("cost", 0)},
			body, result, error,
		)

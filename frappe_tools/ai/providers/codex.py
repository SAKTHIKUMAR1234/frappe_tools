"""ChatGPT Codex Responses stream."""

import json

import frappe
from requests import Response

from frappe_tools.ai.providers import ProviderError, error_message, token_usage

BASE_URL = "https://chatgpt.com/backend-api"


def build_request(
	account: dict, model: str, system: str, user: str, images: list[str], schema: dict | None, options: dict
) -> tuple:
	return build_turn(account, model, system, [user_message(user, images)], schema, options)


def user_message(user: str, images: list[str]) -> dict:
	content = [{"type": "input_text", "text": user}]
	content.extend({"type": "input_image", "image_url": url} for url in images)
	return {"role": "user", "content": content}


def build_turn(
	account: dict, model: str, system: str, messages: list, schema: dict | None, options: dict,
	tools: list[dict] | None = None, final: bool = False,
) -> tuple:
	"""Build one stateless turn: with store off, `messages` must hold the whole conversation."""
	body = {
		"model": model,
		"instructions": system + ("\nReturn only JSON." if schema is None else ""),
		"input": list(messages),
		"stream": True,
		"store": False,
	}
	if schema is not None:
		body["text"] = {"format": {"type": "json_schema", "name": "result", "schema": schema, "strict": True}}
	if tools:
		body["tools"] = [{"type": "function", "strict": False, **tool} for tool in tools]
		body["tool_choice"] = "none" if final else "auto"
	headers = {
		"Authorization": f"Bearer {account['access_token']}",
		"chatgpt-account-id": account["chatgpt_account_id"],
		"OpenAI-Beta": "responses=experimental",
		"originator": "codex_cli_rs",
		"accept": "text/event-stream",
	}
	return (account.get("base_url") or BASE_URL).rstrip("/") + "/codex/responses", headers, body


def parse_response(response: Response) -> tuple:
	deltas, items = [], []
	for event in _events(response):
		kind = event.get("type")
		if kind in ("error", "response.failed", "response.incomplete"):
			result = event.get("response") or event
			raise ProviderError(
				error_message(result) or event.get("message") or frappe._("Codex response failed or was incomplete"),
				result=event, raw_text="".join(deltas) or _completed_text(result),
				finish_reason="incomplete" if kind == "response.incomplete" else "failed",
			)
		if kind == "response.output_text.delta":
			deltas.append(event.get("delta", ""))
		if kind == "response.output_item.done":
			items.append(event.get("item") or {})
		if kind == "response.completed":
			result = event["response"]
			result["output"] = result.get("output") or items
			text = "".join(deltas) or _completed_text(result)
			return text, token_usage(result.get("usage") or {}), result.get("status", "completed"), result
	raise ProviderError(frappe._("Codex stream ended without response.completed"))


def _events(response):
	for line in response.iter_lines():
		if isinstance(line, bytes):
			line = line.decode("utf-8")
		if line.startswith("data:"):
			data = line[5:].strip()
			if data and data != "[DONE]":
				yield json.loads(data)


def _completed_text(result):
	return "".join(
		part.get("text", "")
		for item in result.get("output", [])
		if item.get("type") == "message"
		for part in item.get("content", [])
		if part.get("type") == "output_text"
	)


def _function_calls(result):
	return [item for item in result.get("output", []) if item.get("type") == "function_call"]


def tool_calls(result: dict) -> list[dict]:
	return [
		{"id": item["call_id"], "name": item["name"], "arguments": item.get("arguments")}
		for item in _function_calls(result)
	]


def continuation(result: dict, outputs: list[tuple[dict, str]]) -> list[dict]:
	"""Resend the calls without server item ids, which do not exist when store is off."""
	calls = [
		{"type": "function_call", "call_id": item["call_id"], "name": item["name"], "arguments": item.get("arguments") or "{}"}
		for item in _function_calls(result)
	]
	return [*calls, *({"type": "function_call_output", "call_id": call["id"], "output": text} for call, text in outputs)]

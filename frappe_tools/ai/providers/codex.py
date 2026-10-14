"""ChatGPT Codex Responses stream."""

import json
import time

import frappe
from requests import Response, Timeout

from frappe_tools.ai.providers import (
	ProviderError,
	chat_parts,
	chat_reply,
	error_message,
	split_system,
	token_usage,
)

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
		"instructions": system + ("\nReturn only JSON." if schema is None and options.get("json_mode", True) else ""),
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


def parse_response(response: Response, deadline: float | None = None) -> tuple:
	"""Read the event stream; raise Timeout when `deadline` (time.monotonic()) passes while reading."""
	deltas, items = [], []
	for event in _events(response, deadline):
		kind = event.get("type")
		if kind in ("error", "response.failed", "response.incomplete"):
			result = event.get("response") or event
			raise ProviderError(
				error_message(result) or event.get("message") or frappe._("Codex response failed or was incomplete"),
				result=event, raw_text="".join(deltas) or _completed_text(result),
				finish_reason="incomplete" if kind == "response.incomplete" else "failed",
				usage=token_usage(result.get("usage") or {}),
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


def _events(response, deadline):
	for line in response.iter_lines():
		if deadline is not None and time.monotonic() >= deadline:
			raise Timeout(frappe._("The AI time budget ended while the answer was streamed"))
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


def chat_messages(messages: list[dict]) -> tuple[str, list[dict]]:
	"""Convert OpenAI chat `messages` to the system text and Responses input items."""
	system, turns = split_system(messages)
	return system, [item for message in turns for item in _chat_items(message)]


def _chat_items(message):
	if message["role"] == "tool":
		return [{"type": "function_call_output", "call_id": message["tool_call_id"], "output": message["content"]}]
	if message["role"] == "assistant":
		text = message.get("content") or ""
		items = [{"role": "assistant", "content": [{"type": "output_text", "text": text}]}] if text else []
		items.extend(
			{"type": "function_call", "call_id": call["id"], "name": call["function"]["name"],
				"arguments": call["function"].get("arguments") or "{}"}
			for call in message.get("tool_calls") or []
		)
		return items
	return [{"role": "user", "content": [_input_part(part) for part in chat_parts(message["content"])]}]


def _input_part(part):
	if part["type"] == "text":
		return {"type": "input_text", "text": part["text"]}
	if part["type"] == "image_url":
		image = part["image_url"]
		return {"type": "input_image", "image_url": image["url"], **({"detail": image["detail"]} if image.get("detail") else {})}
	raise ValueError(frappe._("OpenAI Codex cannot read {0} content").format(part["type"]))


def assistant_message(result: dict, text: str) -> dict:
	return chat_reply(text, tool_calls(result))

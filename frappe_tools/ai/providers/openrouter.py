"""OpenRouter chat completions."""

import frappe
from requests import Response

from frappe_tools.ai.providers import ProviderError, error_message

BASE_URL = "https://openrouter.ai/api/v1"


def build_request(
	account: dict, model: str, system: str, user: str, images: list[str], schema: dict | None, options: dict
) -> tuple:
	return build_turn(account, model, system, [user_message(user, images)], schema, options)


def user_message(user: str, images: list[str]) -> dict:
	content = [{"type": "text", "text": user}]
	content.extend({"type": "image_url", "image_url": {"url": url}} for url in images)
	return {"role": "user", "content": content}


def build_turn(
	account: dict, model: str, system: str, messages: list, schema: dict | None, options: dict,
	tools: list[dict] | None = None, final: bool = False,
) -> tuple:
	"""Build one chat turn; `final` forbids further tool calls."""
	body = {
		"model": model,
		"messages": [{"role": "system", "content": system}, *messages],
		"temperature": options["temperature"],
		"max_tokens": options["max_tokens"],
		"response_format": {"type": "json_object"},
		"usage": {"include": True},
	}
	if schema is not None:
		body["response_format"] = {
			"type": "json_schema",
			"json_schema": {"name": "result", "strict": True, "schema": schema},
		}
	if tools:
		body["tools"] = [{"type": "function", "function": tool} for tool in tools]
		body["tool_choice"] = "none" if final else "auto"
	headers = {
		"Authorization": f"Bearer {account['api_key']}",
		"Content-Type": "application/json",
		"HTTP-Referer": frappe.utils.get_url(),
		"X-Title": "Frappe Tools Document Extraction",
	}
	return (account.get("base_url") or BASE_URL).rstrip("/") + "/chat/completions", headers, body


def parse_response(response: Response) -> tuple:
	result = response.json()
	if result.get("error"):
		error = result["error"]
		code = error.get("code") if isinstance(error, dict) else None
		raise ProviderError(error_message(result) or frappe._("OpenRouter returned an error"),
			code if isinstance(code, int) else None, result=result)
	choice = (result.get("choices") or [{}])[0]
	return (
		choice.get("message", {}).get("content") or "",
		result.get("usage") or {},
		choice.get("finish_reason"),
		result,
	)


def _message(result):
	return ((result.get("choices") or [{}])[0]).get("message") or {}


def tool_calls(result: dict) -> list[dict]:
	return [
		{"id": call["id"], "name": call["function"]["name"], "arguments": call["function"].get("arguments")}
		for call in _message(result).get("tool_calls") or []
	]


def continuation(result: dict, outputs: list[tuple[dict, str]]) -> list[dict]:
	"""Return the assistant tool-call message and one tool message per output."""
	message = _message(result)
	reply = {"role": "assistant", "content": message.get("content") or "", "tool_calls": message["tool_calls"]}
	return [reply, *({"role": "tool", "tool_call_id": call["id"], "content": text} for call, text in outputs)]

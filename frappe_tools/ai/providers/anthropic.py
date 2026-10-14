"""Anthropic Messages with native JSON schema output."""

import re

import frappe
from requests import Response

from frappe_tools.ai.providers import ProviderError, error_message, token_usage

BASE_URL = "https://api.anthropic.com"
CLAUDE_CODE_VERSION = "2.1.280"
OAUTH_BETAS = "claude-code-20250219,oauth-2025-04-20,fine-grained-tool-streaming-2025-05-14,interleaved-thinking-2025-05-14"


def build_request(
	account: dict, model: str, system: str, user: str, images: list[str], schema: dict | None, options: dict
) -> tuple:
	return build_turn(account, model, system, [user_message(user, images)], schema, options)


def user_message(user: str, images: list[str]) -> dict:
	return {"role": "user", "content": [{"type": "text", "text": user}, *[_image(url) for url in images]]}


def build_turn(
	account: dict, model: str, system: str, messages: list, schema: dict | None, options: dict,
	tools: list[dict] | None = None, final: bool = False,
) -> tuple:
	"""Build one Messages turn; `final` forbids further tool calls."""
	body = {
		"model": model,
		"system": [{"type": "text", "text": system + "\nReturn only JSON."}],
		"messages": list(messages),
		"temperature": options["temperature"],
		"max_tokens": options["max_tokens"],
	}
	if schema is not None:
		body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
	if tools:
		body["tools"] = [
			{"name": tool["name"], "description": tool["description"], "input_schema": tool["parameters"]}
			for tool in tools
		]
		body["tool_choice"] = {"type": "none" if final else "auto"}
	headers = {"anthropic-version": "2023-06-01", "Content-Type": "application/json"}
	if account["auth_type"] == "Setup Token":
		headers.update(
			{
				"Authorization": f"Bearer {account['api_key']}",
				"anthropic-beta": OAUTH_BETAS,
				"x-app": "cli",
				"user-agent": f"claude-cli/{CLAUDE_CODE_VERSION}",
				"accept": "application/json",
				"anthropic-dangerous-direct-browser-access": "true",
			}
		)
		body["system"][0:0] = [
			{
				"type": "text",
				"text": f"x-anthropic-billing-header: cc_version={CLAUDE_CODE_VERSION}; cc_entrypoint=sdk-cli;",
			},
			{"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."},
		]
	else:
		headers["x-api-key"] = account["api_key"]
	return (account.get("base_url") or BASE_URL).rstrip("/") + "/v1/messages", headers, body


def _image(url):
	match = re.fullmatch(r"data:(image/[\w.+-]+);base64,([A-Za-z0-9+/=\s]+)", url)
	if not match:
		raise ValueError(frappe._("Anthropic images must be base64 image data URLs"))
	return {"type": "image", "source": {"type": "base64", "media_type": match[1], "data": match[2]}}


def parse_response(response: Response) -> tuple:
	result = response.json()
	if result.get("error"):
		raise ProviderError(error_message(result) or frappe._("Anthropic returned an error"), result=result)
	text = "".join(part.get("text", "") for part in result.get("content", []) if part.get("type") == "text")
	return text, token_usage(result.get("usage") or {}), result.get("stop_reason"), result


def _tool_uses(result):
	return [part for part in result.get("content", []) if part.get("type") == "tool_use"]


def tool_calls(result: dict) -> list[dict]:
	return [{"id": part["id"], "name": part["name"], "arguments": part.get("input")} for part in _tool_uses(result)]


def continuation(result: dict, outputs: list[tuple[dict, str]]) -> list[dict]:
	"""Return the assistant content and one user message with all tool results."""
	results = [{"type": "tool_result", "tool_use_id": call["id"], "content": text} for call, text in outputs]
	return [{"role": "assistant", "content": result["content"]}, {"role": "user", "content": results}]

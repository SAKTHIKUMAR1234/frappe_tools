"""Genie as a model: one signed call to the Genie hooks device, answered by a Genie playbook.

The call is OpenAI chat shaped. Its JSON text is signed (X-Hub-Signature-256) and sent as {"request": text}, so
the signature holds however the outer body is encoded on its way. Genie reads text only: no images. With tools
offered, Genie answers tool calls in the same shape as the other providers. Genie is also a conversation agent: it
takes a whole chat by hand-over (see frappe_tools.ai.conversation).
"""

import hashlib
import hmac
import ipaddress
import json
import time
import uuid
from urllib.parse import urlsplit

import frappe
from requests import Response

from frappe_tools.ai.providers import ProviderError, chat_parts, chat_reply, error_message, split_system, token_usage

DEFAULT_TIMEOUT = 50
# The device answers this many seconds before the HTTP request would time out, so its own error arrives.
ANSWER_MARGIN = 3
# http is allowed only to these hosts, as Relay's Genie URL rule: loopback, private and CGNAT (Tailscale) addresses.
CGNAT = ipaddress.ip_network("100.64.0.0/10")


def check_url(url: str | None) -> None:
	"""Allow https, and http only to localhost or a loopback, private or 100.64.0.0/10 address."""
	parts = urlsplit(url or "")
	if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
		raise ProviderError(frappe._("Set Base URL to the Genie hooks device URL, http or https, without credentials"), 400)
	if parts.scheme == "https" or parts.hostname == "localhost":
		return
	try:
		address = ipaddress.ip_address(parts.hostname)
	except ValueError:
		address = None
	if not address or not (address.is_loopback or address.is_private or address in CGNAT):
		raise ProviderError(frappe._("The Genie Base URL must use https, except for a loopback, private or 100.64.0.0/10 address"), 400)


def build_request(
	account: dict, model: str, system: str, user: str, images: list[str], schema: dict | None, options: dict
) -> tuple:
	return build_turn(account, model, system, [user_message(user, images)], schema, options)


def user_message(user: str, images: list[str]) -> dict:
	if images:
		raise ValueError(frappe._("Genie reads text only, not images"))
	return {"role": "user", "content": user}


def build_turn(
	account: dict, model: str, system: str, messages: list, schema: dict | None, options: dict,
	tools: list[dict] | None = None, final: bool = False,
) -> tuple:
	"""Build one signed call; `final` forbids further tool calls."""
	check_url(account.get("base_url"))
	call = {
		"version": 1,
		"id": uuid.uuid4().hex,
		"sent_at": int(time.time()),
		"model": model,
		"system": system,
		"messages": list(messages),
		"response_format": response_format(schema, options),
		"max_tokens": options["max_tokens"],
		"timeout_s": wait_seconds(account, options),
	}
	if tools:
		call["tools"] = tools
		call["tool_choice"] = "none" if final else "auto"
	text = json.dumps(call, ensure_ascii=False, separators=(",", ":"))
	body = {"request": text}
	if tools:
		# client.execute reads tool calls only when the body names tools; the signed call holds them.
		body["tools"] = [tool["name"] for tool in tools]
	headers = {
		"Content-Type": "application/json",
		"Idempotency-Key": call["id"],
		"X-Hub-Signature-256": sign(account["api_key"], text),
	}
	return account["base_url"].rstrip("/") + "/provider", headers, body


def hand_over_request(account: dict, route: str, payload: dict) -> tuple[str, dict, bytes]:
	"""Build a signed hand-over of a whole conversation to <Base URL>/<route>; the raw body is signed."""
	check_url(account.get("base_url"))
	text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
	headers = {"Content-Type": "application/json", "X-Hub-Signature-256": sign(account["api_key"], text)}
	return account["base_url"].rstrip("/") + "/" + route.lstrip("/"), headers, text.encode()


def sign(secret: str, text: str) -> str:
	return "sha256=" + hmac.new(secret.encode(), text.encode(), hashlib.sha256).hexdigest()


def response_format(schema: dict | None, options: dict) -> dict:
	if schema is not None:
		return {"type": "json_schema", "schema": schema}
	return {"type": "json_object" if options.get("json_mode", True) else "text"}


def wait_seconds(account: dict, options: dict) -> int:
	"""How long Genie may take: the account's timeout, within the request's timeout and the time left for it
	(credentials carry what is left of the caller's deadline), less the answer margin."""
	limits = (account.get("request_timeout") or DEFAULT_TIMEOUT, options["timeout"], account.get("timeout"))
	limit = min(limit for limit in limits if limit)
	return max(int(limit) - ANSWER_MARGIN, 1)


def parse_response(response: Response) -> tuple:
	result = response.json()
	if result.get("error"):
		raise ProviderError(error_message(result) or frappe._("Genie returned an error"), result=result)
	finish = result.get("finish_reason")
	return result.get("content") or "", token_usage(result.get("usage") or {}), finish, result


def tool_calls(result: dict) -> list[dict]:
	return [
		{"id": call["id"], "name": call["name"], "arguments": call.get("arguments")}
		for call in result.get("tool_calls") or []
	]


def continuation(result: dict, outputs: list[tuple[dict, str]]) -> list[dict]:
	"""Return the assistant tool-call message and one tool message per output."""
	reply = chat_reply(result.get("content") or "", tool_calls(result))
	return [reply, *({"role": "tool", "tool_call_id": call["id"], "content": text} for call, text in outputs)]


def chat_messages(messages: list[dict]) -> tuple[str, list[dict]]:
	"""Return the system text and the turns of OpenAI chat `messages` with the fields Genie reads, text only."""
	system, turns = split_system(messages)
	return system, [_turn(message) for message in turns]


def _turn(message: dict) -> dict:
	turn = {"role": message["role"], "content": _text(message.get("content"))}
	if message.get("tool_calls"):
		turn["tool_calls"] = chat_reply("", [
			{"id": call["id"], "name": call["function"]["name"], "arguments": call["function"].get("arguments")}
			for call in message["tool_calls"]
		])["tool_calls"]
	if message.get("tool_call_id"):
		turn["tool_call_id"] = message["tool_call_id"]
	return turn


def _text(content) -> str:
	texts = []
	for part in chat_parts(content):
		if part["type"] != "text":
			raise ValueError(frappe._("Genie cannot read {0} content").format(part["type"]))
		texts.append(part["text"])
	return "\n".join(texts)


def assistant_message(result: dict, text: str) -> dict:
	return chat_reply(text, tool_calls(result))

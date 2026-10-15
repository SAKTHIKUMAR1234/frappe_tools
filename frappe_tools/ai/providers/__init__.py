"""Provider response types."""

import json


class ProviderError(ValueError):
	"""A provider rejected or failed a request."""

	def __init__(self, message: str, status_code: int | None = None, *, result=None,
		raw_text: str = "", finish_reason: str | None = None, usage: dict | None = None):
		super().__init__(message)
		self.status_code = status_code
		self.result = result
		self.raw_text = raw_text
		self.finish_reason = finish_reason
		self.usage = usage


def error_message(result) -> str:
	"""Extract a provider's error message before redaction and length limiting."""
	error = result.get("error") if isinstance(result, dict) else None
	if isinstance(error, dict):
		return str(error.get("message") or json.dumps(error))
	return str(error) if error else ""


def token_usage(usage: dict) -> dict:
	prompt = usage.get("input_tokens", 0)
	completion = usage.get("output_tokens", 0)
	return {
		"prompt_tokens": prompt,
		"completion_tokens": completion,
		"total_tokens": usage.get("total_tokens", prompt + completion),
		"cost": 0,
	}


def split_system(messages: list[dict]) -> tuple[str, list[dict]]:
	"""Split OpenAI chat messages into the joined system text and the other messages."""
	system = "\n\n".join(message["content"] for message in messages if message["role"] == "system")
	return system, [message for message in messages if message["role"] != "system"]


def chat_parts(content) -> list[dict]:
	"""Return chat message content as a list of OpenAI content parts."""
	return [{"type": "text", "text": content}] if isinstance(content, str) else list(content or [])


def chat_reply(text: str, calls: list[dict]) -> dict:
	"""Return an OpenAI chat assistant message for a reply and its tool calls."""
	message = {"role": "assistant", "content": text}
	if calls:
		message["tool_calls"] = [
			{"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": _json_text(call["arguments"])}}
			for call in calls
		]
	return message


def _json_text(arguments) -> str:
	return arguments if isinstance(arguments, str) else json.dumps(arguments or {})

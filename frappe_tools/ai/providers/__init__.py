"""Provider response types."""

import json


class ProviderError(ValueError):
	"""A provider rejected or failed a request."""

	def __init__(self, message: str, status_code: int | None = None, *, result=None,
		raw_text: str = "", finish_reason: str | None = None):
		super().__init__(message)
		self.status_code = status_code
		self.result = result
		self.raw_text = raw_text
		self.finish_reason = finish_reason


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

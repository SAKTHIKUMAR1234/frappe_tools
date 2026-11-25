"""Hand a whole conversation to an agent model, which answers the chat itself instead of one model turn.

Only a provider adapter with `hand_over_request(account, route, payload)` is a conversation agent."""

import frappe
import requests

from frappe_tools.ai import accounts, client
from frappe_tools.ai.providers import ProviderError


class HandOverError(Exception):
	"""The agent did not accept the hand-over."""


def agent_route(ai_model: str):
	"""Return (account, adapter) of an enabled AI Model whose enabled account is a conversation agent, else None."""
	for account, _model in client._usable_route(ai_model):
		adapter = client.PROVIDERS.get(account.provider)
		if hasattr(adapter, "hand_over_request"):
			return account, adapter
	return None


def is_conversation_agent(ai_model: str) -> bool:
	return agent_route(ai_model) is not None


def hand_over(ai_model: str, route: str, payload: dict, headers: dict | None = None, timeout: int = 10) -> None:
	"""Post `payload` to the agent's `route` once; raise HandOverError with a short reason when it is not accepted."""
	found = agent_route(ai_model)
	if not found:
		raise HandOverError(frappe._("AI Model {0} is not an enabled conversation agent").format(ai_model))
	account, adapter = found
	try:
		url, signed_headers, body = adapter.hand_over_request(accounts.credentials(account, timeout), route, payload)
	except ProviderError as error:
		raise HandOverError(str(error)) from None
	try:
		response = requests.post(
			url, data=body, headers={**(headers or {}), **signed_headers}, timeout=timeout, allow_redirects=False
		)
	except requests.RequestException as error:
		raise HandOverError(type(error).__name__) from None
	response.close()
	if not 200 <= response.status_code < 300:
		raise HandOverError(f"HTTP {response.status_code}")

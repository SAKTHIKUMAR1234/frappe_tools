"""Tool-calling model loop with a shared tool budget, a deadline and credential redaction."""

import json
from collections.abc import Callable
from dataclasses import dataclass

import frappe

from frappe_tools.ai import client
from frappe_tools.utils import llm

MAX_TOOL_CALLS = 12
MAX_RESULT_CHARS = 20000
TRUNCATED = " ... [truncated]"
LIMIT_REACHED = "Tool call limit reached. Give the final JSON answer now."
SITE_SECRETS = ("db_password", "encryption_key")
TOOL_ERRORS = (frappe.ValidationError, frappe.PermissionError, ValueError, TypeError, KeyError)


@dataclass(frozen=True)
class Tool:
	"""A function the model may call; `run` takes the parsed arguments as keywords."""

	name: str
	description: str
	parameters: dict
	run: Callable[..., object]

	@property
	def definition(self) -> dict:
		return {"name": self.name, "description": self.description, "parameters": self.parameters}


def call_agent(
	task: str,
	system_prompt: str,
	user_prompt: str,
	*,
	tools: list[Tool],
	schema: dict,
	deadline: float,
	route: tuple[str, str] | None = None,
	phase: str | None = None,
	target_doctype: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	run_id: str | None = None,
) -> dict:
	"""Run a tool loop on the chosen route, then the task models, until a final answer matches `schema`.

	Every model turn is logged. `deadline` is a time.monotonic() value for the whole call.
	"""
	settings = client.task_settings(task)
	run = AgentRun(tools, get_known_secrets(settings), deadline)
	prompts = (system_prompt, llm.redact_secrets(user_prompt, run.secrets), schema)
	options = client.task_options(settings)
	context = dict(
		extraction=None,
		target_doctype=target_doctype,
		phase=phase,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		run_id=run_id,
	)
	last_error = frappe._("No enabled AI model and account for this task")
	for account, model in client.routes(settings, task, route):
		outcome = run.run_route(account, model, prompts, options, context)
		if outcome["ok"]:
			return outcome["result"]
		last_error = outcome["error"]
	return frappe.throw(frappe._("AI call failed: {0}").format(last_error))


def get_known_secrets(settings) -> list[str]:
	"""Return the provider credentials and site secrets that must never reach a model."""
	secrets = [frappe.conf.get(key) for key in SITE_SECRETS]
	secrets.extend(_passwords(settings))
	for name in frappe.get_all("AI Provider Account", pluck="name"):
		secrets.extend(_passwords(frappe.get_doc("AI Provider Account", name)))
	return [secret for secret in secrets if secret]


def _passwords(doc):
	return [
		doc.get_password(field.fieldname, raise_exception=False)
		for field in doc.meta.fields
		if field.fieldtype == "Password"
	]


class AgentRun:
	"""One tool loop across routes; the tool budget and deadline are shared by all routes."""

	def __init__(self, tools: list[Tool], secrets: list[str], deadline: float, max_tool_calls: int = MAX_TOOL_CALLS):
		self.tools = {tool.name: tool for tool in tools}
		self.secrets = secrets
		self.deadline = deadline
		self.calls_left = max_tool_calls

	def run_route(self, account, model: str, prompts: tuple, options: dict, context: dict) -> dict:
		"""Return the outcome of the final turn, or of the turn that failed."""
		system, user, schema = prompts
		adapter = client.PROVIDERS[account.get("provider")]
		messages = [adapter.user_message(user, [])]
		definitions = [tool.definition for tool in self.tools.values()]
		while True:
			final = self.calls_left <= 0

			def build(adapter, credentials, model):
				return adapter.build_turn(credentials, model, system, messages, schema, options, definitions, final)

			outcome = client.try_model(account, model, build, options, context, self.deadline)
			calls = outcome["result"]["tool_calls"] if outcome["ok"] else []
			if not calls:
				return outcome
			if final:
				return {"ok": False, "retryable": False, "error": frappe._("Model called tools after the limit")}
			messages.extend(adapter.continuation(outcome["result"]["response"], self.run_tools(calls)))

	def run_tools(self, calls: list[dict]) -> list[tuple[dict, str]]:
		outputs = []
		for call in calls:
			if self.calls_left <= 0:
				outputs.append((call, json.dumps({"error": LIMIT_REACHED})))
				continue
			self.calls_left -= 1
			outputs.append((call, self.run_tool(call)))
		return outputs

	def run_tool(self, call: dict) -> str:
		"""Run one call and return its redacted, size-capped JSON result."""
		try:
			tool = self.tools.get(call["name"])
			if not tool:
				raise ValueError(f"Unknown tool {call['name']}")
			value = tool.run(**_arguments(call["arguments"]))
		except TOOL_ERRORS as exc:
			value = {"error": str(exc) or type(exc).__name__}
		text = json.dumps(llm.redact_secrets(value, self.secrets), default=str, ensure_ascii=False)
		text = llm.redact_secrets(text, self.secrets)
		if len(text) > MAX_RESULT_CHARS:
			text = text[: MAX_RESULT_CHARS - len(TRUNCATED)] + TRUNCATED
		return text


def _arguments(value) -> dict:
	arguments = json.loads(value or "{}") if isinstance(value, str) else value or {}
	if not isinstance(arguments, dict):
		raise ValueError("Tool arguments must be a JSON object")
	return arguments

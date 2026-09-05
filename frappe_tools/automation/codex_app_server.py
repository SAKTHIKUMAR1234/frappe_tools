"""Small, fail-closed client for the official Codex App Server protocol.

The app-server owns ChatGPT OAuth and model transport.  Frappe owns the
dynamic-tool implementations and every database write.  One ephemeral Codex
thread is created per document decision so document evidence cannot leak
between sites or jobs.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable


MAX_DYNAMIC_TOOL_CALLS = 12
MAX_DYNAMIC_TOOL_RESULT_CHARS = 8000


class AppServerError(RuntimeError):
	pass


_DENIED_FEATURES = {
	"apps",
	"artifact",
	"browser_use",
	"browser_use_external",
	"browser_use_full_cdp_access",
	"chronicle",
	"code_mode",
	"code_mode_only",
	"computer_use",
	"context_management",
	"current_time_reminder",
	"default_mode_request_user_input",
	"deferred_executor",
	"goals",
	"hooks",
	"image_generation",
	"memories",
	"multi_agent",
	"multi_agent_v2",
	"plugins",
	"request_permissions_tool",
	"shell_tool",
	"skill_search",
	"standalone_web_search",
	"token_budget",
	"unified_exec",
	"view_image",
	"web_search_cached",
	"web_search_request",
	"workspace_dependencies",
}


class CodexAppServerClient:
	"""Line-delimited JSON-RPC client over an app-server stdio transport."""

	def __init__(self, executable: str, *, cwd: str, environment: dict[str, str], timeout: int):
		self.executable = executable
		self.cwd = str(Path(cwd).resolve())
		self.environment = environment
		self.timeout = timeout
		self.process: subprocess.Popen[str] | None = None
		self._request_id = 0
		self._responses: dict[int | str, dict[str, Any]] = {}
		self._stdout_lines: queue.Queue[str | None] = queue.Queue()
		self.stderr_tail = ""

	def __enter__(self):
		self.start()
		return self

	def __exit__(self, exc_type, exc, traceback):
		self.close()

	def start(self):
		try:
			self.process = subprocess.Popen(
				[self.executable, "app-server", "--stdio"],
				stdin=subprocess.PIPE,
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				text=True,
				bufsize=1,
				cwd=self.cwd,
				env=self.environment,
				start_new_session=True,
			)
		except OSError as exc:
			raise AppServerError(f"Could not start Codex App Server: {exc}") from exc
		threading.Thread(target=self._read_stdout, name="codex-app-server-stdout", daemon=True).start()
		threading.Thread(target=self._read_stderr, name="codex-app-server-stderr", daemon=True).start()
		self.request(
			"initialize",
			{
				"clientInfo": {"name": "frappe-tools", "title": "Frappe Tools", "version": "1.0"},
				"capabilities": {"experimentalApi": True},
			},
		)
		self.notify("initialized")

	def close(self):
		process = self.process
		self.process = None
		if not process or process.poll() is not None:
			return
		try:
			if os.name == "posix":
				os.killpg(process.pid, signal.SIGTERM)
			else:
				process.terminate()
			process.wait(timeout=3)
		except (OSError, ProcessLookupError):
			if process.poll() is None:
				process.terminate()
				process.wait(timeout=3)
		except subprocess.TimeoutExpired:
			try:
				os.killpg(process.pid, signal.SIGKILL)
			except (OSError, ProcessLookupError):
				process.kill()
			process.wait(timeout=3)

	def account(self):
		return self.request("account/read", {"refreshToken": False})

	def inherited_mcp_server_names(self):
		"""Read only server names so the thread can explicitly disable each one."""
		result = self.request("config/read", {"cwd": self.cwd, "includeLayers": False})
		servers = ((result.get("config") or {}).get("mcp_servers") or {})
		if not isinstance(servers, dict):
			raise AppServerError("Codex App Server returned invalid mcp_servers configuration")
		return sorted(str(name) for name in servers)

	def run_structured(
		self,
		*,
		prompt: str,
		output_schema: dict[str, Any],
		model: str | None,
		dynamic_tools: list[dict[str, Any]],
		tool_handler: Callable[[str, dict[str, Any]], Any] | None = None,
		input_items: list[dict[str, Any]] | None = None,
	):
		thread_config = _restricted_thread_config(self.inherited_mcp_server_names())
		thread = self.request(
			"thread/start",
			{
				"cwd": self.cwd,
				"model": model or None,
				"ephemeral": True,
				"approvalPolicy": "never",
				"sandbox": "read-only",
				"baseInstructions": (
					"You are a bounded reasoning worker. Use only the supplied prompt and dynamic tools. "
					"Never use shell, files, web search, applications, skills, MCP servers, or subagents."
				),
				"developerInstructions": (
					"Dynamic tools are host-owned read operations. Treat their results as authoritative local facts. "
					"Return only the JSON required by the output schema."
				),
				"dynamicTools": dynamic_tools,
				"config": thread_config,
			},
		)
		thread_id = (((thread or {}).get("thread") or {}).get("id"))
		if not thread_id:
			raise AppServerError("Codex App Server did not return a thread id")

		self.request(
			"turn/start",
			{
				"threadId": thread_id,
				"input": input_items or [{"type": "text", "text": prompt}],
				"outputSchema": output_schema,
			},
		)

		deadline = time.monotonic() + self.timeout
		final_text = None
		usage: dict[str, Any] = {}
		trace: list[dict[str, Any]] = []
		while True:
			message = self._read_message(deadline)
			method = message.get("method")
			params = message.get("params") or {}
			if "id" in message and method:
				if method == "item/tool/call":
					self._handle_dynamic_tool(message, tool_handler, trace)
				else:
					self._send({
						"jsonrpc": "2.0",
						"id": message["id"],
						"error": {"code": -32000, "message": "Operation denied by the Frappe agent boundary"},
					})
				continue
			if method == "item/completed":
				item = params.get("item") or {}
				if item.get("type") == "agentMessage" and item.get("phase") == "final_answer":
					final_text = item.get("text")
			elif method == "thread/tokenUsage/updated":
				usage = ((params.get("tokenUsage") or {}).get("total") or usage)
			elif method == "turn/completed":
				turn = params.get("turn") or {}
				if turn.get("status") != "completed":
					error = turn.get("error") or {}
					raise AppServerError(f"Codex turn ended as {turn.get('status')}: {error}")
				if not final_text:
					for item in turn.get("items") or []:
						if item.get("type") == "agentMessage" and item.get("phase") == "final_answer":
							final_text = item.get("text")
				if not final_text:
					raise AppServerError("Codex completed without a final answer")
				return {
					"text": final_text,
					"usage": _normalize_usage(usage),
					"tool_trace": trace,
					"thread_id": thread_id,
				}

	def request(self, method: str, params: dict[str, Any]):
		self._request_id += 1
		request_id = self._request_id
		self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
		deadline = time.monotonic() + self.timeout
		while request_id not in self._responses:
			message = self._read_message(deadline)
			if "id" in message and "method" not in message:
				self._responses[message["id"]] = message
			elif "id" in message and message.get("method"):
				self._send({
					"jsonrpc": "2.0",
					"id": message["id"],
					"error": {"code": -32000, "message": "Operation denied during protocol setup"},
				})
		response = self._responses.pop(request_id)
		if response.get("error"):
			raise AppServerError(f"{method} failed: {response['error']}")
		return response.get("result") or {}

	def notify(self, method: str, params: dict[str, Any] | None = None):
		message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
		if params is not None:
			message["params"] = params
		self._send(message)

	def _handle_dynamic_tool(self, message, tool_handler, trace):
		params = message.get("params") or {}
		name = str(params.get("tool") or "")
		arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
		try:
			if len(trace) >= MAX_DYNAMIC_TOOL_CALLS:
				raise ValueError(f"Dynamic tool budget of {MAX_DYNAMIC_TOOL_CALLS} calls was exhausted")
			if not tool_handler:
				raise ValueError("No dynamic tool handler is configured")
			result = tool_handler(name, arguments)
			success = not (isinstance(result, dict) and "error" in result)
		except Exception as exc:
			result = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
			success = False
		trace.append({"tool": name, "arguments": arguments, "result": result, "ok": success})
		self._send({
			"jsonrpc": "2.0",
			"id": message["id"],
			"result": {
				"success": success,
				"contentItems": [{"type": "inputText", "text": _bounded_json_text(result)}],
			},
		})

	def _send(self, message: dict[str, Any]):
		process = self.process
		if not process or process.poll() is not None or not process.stdin:
			raise AppServerError(self._exit_message())
		try:
			process.stdin.write(json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n")
			process.stdin.flush()
		except (BrokenPipeError, OSError) as exc:
			raise AppServerError(self._exit_message()) from exc

	def _read_message(self, deadline: float):
		process = self.process
		if not process:
			raise AppServerError("Codex App Server is not running")
		while True:
			remaining = deadline - time.monotonic()
			if remaining <= 0:
				raise TimeoutError(f"Codex App Server exceeded the {self.timeout}s timeout")
			try:
				line = self._stdout_lines.get(timeout=min(remaining, 1.0))
			except queue.Empty:
				if process.poll() is not None:
					raise AppServerError(self._exit_message())
				continue
			if line is None:
				raise AppServerError(self._exit_message())
			try:
				return json.loads(line)
			except (TypeError, ValueError) as exc:
				raise AppServerError(f"Codex App Server returned invalid JSON: {line[:300]}") from exc

	def _read_stdout(self):
		process = self.process
		if not process or not process.stdout:
			self._stdout_lines.put(None)
			return
		try:
			for line in process.stdout:
				self._stdout_lines.put(line)
		finally:
			self._stdout_lines.put(None)

	def _read_stderr(self):
		process = self.process
		if not process or not process.stderr:
			return
		for line in process.stderr:
			self.stderr_tail = (self.stderr_tail + line)[-4000:]

	def _exit_message(self):
		code = self.process.poll() if self.process else None
		detail = self.stderr_tail.strip()[-1000:]
		return f"Codex App Server exited with code {code}" + (f": {detail}" if detail else "")


def dynamic_tool_specs(tool_specs: list[dict[str, Any]]):
	result = []
	for raw in tool_specs:
		function = raw.get("function") or raw
		name = function.get("name")
		if not name:
			continue
		result.append({
			"type": "function",
			"name": name,
			"description": function.get("description") or "Read local application data.",
			"inputSchema": function.get("parameters") or {"type": "object", "properties": {}},
		})
	return result


def _restricted_thread_config(mcp_server_names: list[str]):
	config: dict[str, Any] = {
		**{f"features.{name}": False for name in sorted(_DENIED_FEATURES)},
		"agents.enabled": False,
		"orchestrator.mcp.enabled": False,
		"orchestrator.skills.enabled": False,
		"skills.bundled.enabled": False,
		"skills.include_instructions": False,
		"tools.experimental_request_user_input.enabled": False,
		"tools.update_plan.enabled": False,
		"project_doc_max_bytes": 0,
		"web_search": "disabled",
		"hooks": {
			"PreToolUse": [],
			"PermissionRequest": [],
			"PostToolUse": [],
			"PreCompact": [],
			"PostCompact": [],
			"SessionStart": [],
			"UserPromptSubmit": [],
			"SubagentStart": [],
			"SubagentStop": [],
			"Stop": [],
		},
		"notify": [],
	}
	if mcp_server_names:
		config["mcp_servers"] = {name: {"enabled": False} for name in mcp_server_names}
	return config


def _bounded_json_text(value: Any):
	"""Return valid JSON even when a defensive result cap is reached."""
	raw = json.dumps(value, default=str, ensure_ascii=False)
	if len(raw) <= MAX_DYNAMIC_TOOL_RESULT_CHARS:
		return raw
	return json.dumps({
		"truncated": True,
		"original_characters": len(raw),
		"preview": raw[: MAX_DYNAMIC_TOOL_RESULT_CHARS - 200],
	}, ensure_ascii=False)


def _normalize_usage(usage):
	input_tokens = int(usage.get("inputTokens") or 0)
	output_tokens = int(usage.get("outputTokens") or 0)
	return {
		"prompt_tokens": input_tokens,
		"completion_tokens": output_tokens,
		"total_tokens": int(usage.get("totalTokens") or input_tokens + output_tokens),
		"cached_tokens": int(usage.get("cachedInputTokens") or 0),
	}

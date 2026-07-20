"""Tool catalog for the agentic execution phase.

An I2A Action can expose a catalog of TOOLS the engine may call to verify and
resolve the extracted data on its own — search ERP records, cross-check a
value, apply the finding — instead of handing the document to a human. Some
tools are existing Frappe methods (e.g. `frappe.client.get_list`); some are
written by the consumer app (e.g. `apply_lr_to_invoice`).

Safety model (the whole point):
  - The model only ever picks a tool by NAME from the catalog. The method path
    is FIXED in config — the model can never name an arbitrary method.
  - Every candidate method must be `@frappe.whitelist`-ed (defense in depth).
  - Tool arguments are DATA: only names declared in the tool's parameters
    schema are accepted, and config `defaults` can never be overridden.
  - Write tools (`kind: "write"`) are permission-checked against the target;
    the run's trusted reference travels via frappe.local (see get_context) so
    it can never be forged through /api/method.
"""

import json

import frappe
from frappe import _


class ToolError(Exception):
	pass


def parse_catalog(action):
	"""Return the action's tool catalog (list of tool dicts), or [] when off."""
	raw = getattr(action, "tools", None)
	if not raw:
		return []
	try:
		cat = raw if isinstance(raw, list) else json.loads(raw)
	except (ValueError, TypeError):
		return []
	out = []
	for t in cat if isinstance(cat, list) else []:
		if isinstance(t, dict) and t.get("name") and t.get("method"):
			out.append(t)
	return out


def function_specs(catalog):
	"""OpenAI/OpenRouter function-calling specs from the catalog."""
	specs = []
	for t in catalog:
		specs.append({
			"type": "function",
			"function": {
				"name": t["name"],
				"description": t.get("description", ""),
				"parameters": t.get("parameters") or {"type": "object", "properties": {}},
			},
		})
	return specs


def _find(catalog, name):
	return next((t for t in catalog if t["name"] == name), None)


def execute(catalog, name, args, context):
	"""Execute one model-chosen tool call. Returns a JSON-serialisable result
	dict; never raises out — tool failures are returned to the model as text so
	it can adapt. `context` carries the trusted run reference (injected, not
	model-supplied) for write tools."""
	tool = _find(catalog, name)
	if not tool:
		return {"error": f"unknown tool '{name}'"}
	method_path = tool["method"]

	# whitelist guard: the method must be a Frappe-whitelisted server method.
	try:
		method = frappe.get_attr(method_path)
	except Exception:
		return {"error": f"tool method not found: {method_path}"}
	if method not in frappe.whitelisted:
		return {"error": f"tool method {method_path} is not whitelisted"}

	# Model args are UNTRUSTED (the model reads documents that may carry
	# injected instructions). Two hard rules:
	#   1. only argument names DECLARED in the tool's parameters schema are
	#      accepted — anything else the model invents is dropped;
	#   2. config `defaults` are LOCKED: a default the model also sends is
	#      list-EXTENDED when both are lists (filters keep their invariants,
	#      e.g. docstatus=1), otherwise the model's value is ignored. The model
	#      can never override doctype/fields/limits fixed in config.
	declared = set(((tool.get("parameters") or {}).get("properties") or {}).keys())
	call_args = dict(tool.get("defaults") or {})
	dropped = []
	if isinstance(args, dict):
		for k, v in args.items():
			if k not in declared:
				dropped.append(k)
				continue
			if k in call_args:
				if isinstance(call_args[k], list) and isinstance(v, list):
					call_args[k] = call_args[k] + v
				else:
					dropped.append(k)
				continue
			call_args[k] = v

	if tool.get("kind") == "write":
		target = tool.get("permission_doctype")
		if target and not frappe.has_permission(target, "write"):
			return {"error": f"no write permission on {target}"}

	# The trusted run reference travels via frappe.local — NOT as a kwarg.
	# A kwarg could be forged through /api/method by any logged-in user; local
	# state can only be set server-side by this engine process.
	frappe.local.i2a_tool_context = context
	try:
		result = method(**call_args)
	except frappe.PermissionError as exc:
		return {"error": f"permission denied: {exc}"}
	except Exception as exc:
		return {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
	finally:
		frappe.local.i2a_tool_context = None

	# keep the payload back to the model bounded and serialisable
	try:
		return json.loads(json.dumps(result, default=str))
	except Exception:
		return {"result": str(result)[:2000]}


def get_context():
	"""Trusted run context for the currently-executing tool call. Returns None
	outside an engine tool call — consumer tool methods MUST treat that as a
	hard error (it means someone invoked the endpoint over HTTP directly)."""
	return getattr(frappe.local, "i2a_tool_context", None)

"""Adapter package discovery and the public UI capability contract."""

import inspect
from pathlib import Path

import frappe


def ui_module(plugin):
	module = type(plugin).__module__
	package = module.removesuffix(".plugin")
	if Path(inspect.getfile(type(plugin))).parent.joinpath("ui", "index.js").is_file():
		return package
	return None


def capabilities(plugin, ctx):
	actions = {}
	for name, spec in plugin.review_actions(ctx).items():
		if not callable(spec.get("handler")):
			raise ValueError(f"Adapter action {name} needs a callable handler")
		actions[name] = {
			"label": spec.get("label") or name,
			"allowed": all(frappe.has_permission(dt, ptype=ptype) for dt, ptype in spec.get("permissions", [])),
		}
	return {"version": 1, "module": ui_module(plugin), "actions": actions}


def setup_adapters():
	"""Validate and initialize installed adapters during normal site migration."""
	from frappe_tools.extractors import get_plugin, registered_targets
	from frappe_tools.extractors.context import ExtractionContext
	from frappe_tools.extractors.workflow import manifest

	result = []
	for target in registered_targets():
		if not frappe.db.exists("DocType", target):
			continue
		plugin, ctx = get_plugin(target), ExtractionContext(target)
		manifest(plugin, ctx)
		from frappe_tools.extractors.phases import definitions
		definitions(plugin, ctx)
		plugin.setup(ctx)
		result.append(plugin.adapter_id())
	return result

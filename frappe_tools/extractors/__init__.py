"""Extraction plugin framework — Registry + discovery.

Plugins register against (system, target_doctype). The pipeline asks for a plugin
via get_plugin(); a DocType with no specific plugin gets the GenericPlugin, which
runs the full pipeline on its own. Adding a system/doctype = a new package under
this one; the core never changes.
"""

import importlib

import frappe

_REGISTRY = {}
_DECLARED = {}
_LOADED = False
_HOOKS = None

# Plugins are contributed by ANY installed app via its hooks.py:
#   doc_extraction_plugins = ["my_app.path.to.plugin", ...]
# frappe_tools is only the common layer; document-specific plugins live in their
# own app (e.g. essdee). The GenericPlugin is the built-in fallback for any DocType.


def register(cls):
	"""Class decorator: register an ExtractionPlugin subclass by (system, doctype)."""
	_DECLARED[f"{cls.__module__}.{cls.__name__}"] = cls
	return cls


def _ensure_loaded():
	global _LOADED, _HOOKS, _REGISTRY
	modules = tuple(frappe.get_hooks("doc_extraction_plugins") or [])
	if _LOADED and _HOOKS == modules:
		return
	for module in modules:
		importlib.import_module(module)
	active = {}
	for cls in _DECLARED.values():
		if not any(cls.__module__ == module or cls.__module__.startswith(module + ".") for module in modules):
			continue
		key = (cls.system, cls.target_doctype)
		if key in active and active[key] is not cls:
			raise RuntimeError(f"More than one installed adapter owns {key}")
		active[key] = cls
	# A worker can serve multiple sites with different installed apps.
	_REGISTRY, _HOOKS, _LOADED = active, modules, True


def get_plugin(target_doctype, system="ERPNext"):
	"""Return a plugin instance for the target — specific if registered, else generic."""
	_ensure_loaded()
	from frappe_tools.extractors.generic.plugin import GenericPlugin
	cls = _REGISTRY.get((system, target_doctype))
	return cls(target_doctype) if cls else GenericPlugin(target_doctype)


def has_plugin(target_doctype, system="ERPNext"):
	_ensure_loaded()
	return (system, target_doctype) in _REGISTRY


def registered_targets(system="ERPNext"):
	"""Targets exposed by code-owned adapters; UI data never defines flows."""
	_ensure_loaded()
	return sorted(doctype for (registered_system, doctype) in _REGISTRY if registered_system == system)

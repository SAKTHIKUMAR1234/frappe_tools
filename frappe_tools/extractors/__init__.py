"""Extraction plugin framework — Registry + discovery.

Plugins register against (system, target_doctype). The pipeline asks for a plugin
via get_plugin(); a DocType with no specific plugin gets the GenericPlugin, which
runs the full pipeline on its own. Adding a system/doctype = a new package under
this one; the core never changes.
"""

import importlib

import frappe

_REGISTRY = {}
_LOADED = False

# Plugin modules to import for self-registration. New plugins add their dotted path.
_PLUGIN_MODULES = (
	"frappe_tools.extractors.erpnext.purchase_invoice.plugin",
	"frappe_tools.extractors.essdee.return_goods.plugin",
)


def register(cls):
	"""Class decorator: register an ExtractionPlugin subclass by (system, doctype)."""
	_REGISTRY[(cls.system, cls.target_doctype)] = cls
	return cls


def _ensure_loaded():
	global _LOADED
	if _LOADED:
		return
	_LOADED = True
	for module in _PLUGIN_MODULES:
		try:
			importlib.import_module(module)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Extraction plugin import failed: {module}")


def get_plugin(target_doctype, system="ERPNext"):
	"""Return a plugin instance for the target — specific if registered, else generic."""
	_ensure_loaded()
	from frappe_tools.extractors.generic.plugin import GenericPlugin
	cls = _REGISTRY.get((system, target_doctype))
	return cls(target_doctype) if cls else GenericPlugin(target_doctype)


def has_plugin(target_doctype, system="ERPNext"):
	_ensure_loaded()
	return (system, target_doctype) in _REGISTRY

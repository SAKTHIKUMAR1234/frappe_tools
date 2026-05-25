"""Adapter registry.

Adapters declare their (system, target_doctype) and self-register. The core
engine looks one up via get_adapter(); if none exists it falls back to the
generic scalar-field behaviour.
"""

import frappe

_REGISTRY = {}
_LOADED = False


def register(cls):
	"""Class decorator: register an ExtractionAdapter subclass."""
	_REGISTRY[(cls.system, cls.target_doctype)] = cls
	return cls


def _ensure_loaded():
	global _LOADED
	if _LOADED:
		return
	_LOADED = True
	# Import adapter modules so they self-register. Each guarded so one broken
	# adapter can't take down the rest.
	for module in ("frappe_tools.adapters.erpnext.purchase_invoice",):
		try:
			frappe.get_module(module)
		except Exception:
			import importlib
			try:
				importlib.import_module(module)
			except Exception:
				frappe.log_error(frappe.get_traceback(), f"Adapter import failed: {module}")


def get_adapter(target_doctype, system="ERPNext"):
	"""Return an adapter instance for the target, or None."""
	_ensure_loaded()
	cls = _REGISTRY.get((system, target_doctype))
	return cls() if cls else None


def has_adapter(target_doctype, system="ERPNext"):
	_ensure_loaded()
	return (system, target_doctype) in _REGISTRY

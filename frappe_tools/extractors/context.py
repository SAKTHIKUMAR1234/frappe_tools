"""ExtractionContext — the app-aware inputs handed to every plugin capability.

Keeps app-awareness (e.g. India Compliance tax handling) clean and gated: a
capability checks ctx.has_app("india_compliance") rather than hard-coding it.
"""

import frappe

SETTINGS_DOCTYPE = "Document Extraction Settings"


class ExtractionContext:
	def __init__(self, target_doctype, system="ERPNext"):
		self.target_doctype = target_doctype
		self.system = system
		self._apps = None
		self._settings = None

	@property
	def installed_apps(self):
		if self._apps is None:
			try:
				self._apps = set(frappe.get_installed_apps())
			except Exception:
				self._apps = set()
		return self._apps

	def has_app(self, app):
		return app in self.installed_apps

	@property
	def settings(self):
		if self._settings is None:
			self._settings = frappe.get_single(SETTINGS_DOCTYPE)
		return self._settings

	def meta(self):
		return frappe.get_meta(self.target_doctype)

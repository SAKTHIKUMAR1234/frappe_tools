"""Base class for per-(system, doctype) extraction adapters.

The core engine (frappe_tools.api.doc_extract) stays document-agnostic. Anything
that differs per target document — which fields to extract, how to resolve/match
them to masters, what to validate, which fields the user must verify, how to
build & post the document, and how to learn — lives in an adapter subclass.

Adapters self-register via the `@register` decorator from frappe_tools.adapters.
"""


class ExtractionAdapter:
	# Identity — (system, target_doctype) is the registry key.
	system = "ERPNext"
	target_doctype = None

	# ----- Extraction shape -------------------------------------------------
	def header_fields(self):
		"""Return an explicit list of header field specs to extract, or None to
		let the core derive them from the DocType's scalar fields.

		Each spec: {fieldname, label, fieldtype, required, options?, link_doctype?, description?}
		"""
		return None

	def line_config(self):
		"""Return {"table": <child table fieldname>, "columns": [<column spec>...]}
		describing the line-item array to extract, or None for header-only docs.

		Column spec: {key, label, type, description?} where `key` is the JSON key
		the LLM returns per line (e.g. description, supplier_code, hsn, qty, uom,
		rate, amount).
		"""
		return None

	def prompt_addendum(self):
		"""Optional extra instructions appended to the LLM prompt for this doctype."""
		return None

	# ----- Resolution / matching -------------------------------------------
	def resolve(self, extraction):
		"""Fill matched values + ranked candidates on the extraction's header
		fields and line rows (in place; caller saves). No return value."""
		return None

	# ----- Validation / review policy --------------------------------------
	def review_policy(self):
		"""Return {fieldname: 'auto' | 'confirm' | 'always'} controlling which
		fields the user must verify. Default: everything is reviewable."""
		return {}

	def validate(self, extraction):
		"""Return a list of human-readable issues blocking creation, or []."""
		return []

	# ----- Document build / post -------------------------------------------
	def build_document(self, extraction):
		"""Create & SAVE (never submit) the target document from the reviewed
		extraction. Return the new document name."""
		raise NotImplementedError

	# ----- Learning ---------------------------------------------------------
	def learn(self, extraction, **kwargs):
		"""Persist a confirmed mapping so future documents auto-match."""
		return None

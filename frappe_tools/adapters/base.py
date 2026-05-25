"""Base class for per-(system, doctype) extraction adapters — THIN hooks only.

The general engine (frappe_tools.api.doc_extract) owns the whole pipeline for
EVERY DocType: schema, LLM call, generic resolution, and document creation.
An adapter overrides ONLY the parts that differ for one (system, doctype) via the
optional hooks below — it never reimplements the pipeline. A DocType with no
adapter still works end-to-end on the general path.
"""


class ExtractionAdapter:
	# Identity — (system, target_doctype) is the registry key.
	system = "ERPNext"
	target_doctype = None

	# ----- extraction shape (optional curation) ----------------------------
	def header_fields(self):
		"""Explicit header field specs, or None to derive from the DocType meta."""
		return None

	def tables(self):
		"""Optional curation of child tables to extract:
		[{"table": <child fieldname>, "label": str, "columns": [{key,label,type,description?}]}]
		Return None to let the engine use the rule book's declared tables with
		columns derived from each child DocType's meta.
		"""
		return None

	def prompt_addendum(self):
		"""Extra instruction text appended to the LLM prompt for this doctype."""
		return None

	# ----- hooks the general engine invokes when present -------------------
	def resolve(self, extraction):
		"""Fill matched values + ranked candidates on header fields and line rows
		(in place; the engine saves). e.g. supplier + item cascade + learning."""
		return None

	def transform(self, extraction):
		"""Reshape extracted rows in place BEFORE the document is built.
		e.g. Purchase Invoice: collapse many item lines into one common item."""
		return None

	def customize_document(self, doc, extraction):
		"""Adjust the freshly-built, UNSAVED target document before insert.
		App-aware: may branch on frappe.get_installed_apps().
		e.g. force tax treatment / override the item's auto-applied tax template."""
		return None

	# ----- validation / review policy --------------------------------------
	def review_policy(self):
		return {}

	def validate(self, extraction):
		"""Return a list of human-readable issues blocking creation, or []."""
		return []

"""ExtractionPlugin — the capability interface.

All capabilities are optional; GenericPlugin provides working defaults and is the
base class for specific plugins, which override only what differs. The pipeline
calls these; it never needs to know which plugin it has.
"""

import json


class ExtractionPlugin:
	# Identity — (system, target_doctype) is the registry key.
	system = "ERPNext"
	target_doctype = None

	def __init__(self, target_doctype=None):
		if target_doctype:
			self.target_doctype = target_doctype

	# ----- extraction shape -------------------------------------------------
	def schema(self, ctx):
		"""Return {"header": [field specs], "tables": [{table, label, columns:[...]}]}."""
		raise NotImplementedError

	def prompt_addendum(self, ctx):
		return None

	# ----- pipeline hooks ---------------------------------------------------
	def resolve(self, ctx, extraction):
		"""Fill matched values + candidates on header fields and line rows (in place)."""
		return None

	def transform(self, ctx, extraction, build):
		"""Reshape the build dict {table: [child_row_dict, ...]} before insert."""
		return None

	def customize(self, ctx, doc, extraction):
		"""Adjust the freshly-built, unsaved target document (app-aware)."""
		return None

	def validate(self, ctx, extraction):
		"""Return a list of human-readable issues blocking creation, or []."""
		return []

	# ----- UI ---------------------------------------------------------------
	def provenance_map(self, extraction):
		"""{"fields": {fieldname: {bbox,page}}, "tables": {table: {row_no: {bbox,page}}}}."""
		return default_provenance_map(extraction)


def default_provenance_map(extraction):
	fields = {}
	for f in extraction.extracted_fields:
		if f.bbox_json:
			try:
				fields[f.fieldname] = {"bbox": json.loads(f.bbox_json), "page": f.source_page}
			except Exception:
				pass
	tables = {}
	for l in extraction.lines:
		if l.bbox_json:
			try:
				tables.setdefault(l.table or "", {})[str(l.row_no)] = {"bbox": json.loads(l.bbox_json), "page": l.source_page}
			except Exception:
				pass
	return {"fields": fields, "tables": tables}

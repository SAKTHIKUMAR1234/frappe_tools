"""GenericPlugin — the first-class default for ANY DocType.

Runs the full pipeline with zero document-specific code: header scalar fields +
all rule-book-declared child tables, light generic Link resolution, build 1:1.
Specific plugins subclass this and override only what differs.
"""

import frappe

from frappe_tools.extractors import schema as S
from frappe_tools.extractors.base import ExtractionPlugin


class GenericPlugin(ExtractionPlugin):
	def schema(self, ctx):
		return {
			"header": S.build_header_schema(ctx.target_doctype),
			"tables": S.build_tables_schema(ctx.target_doctype),
		}

	def resolve(self, ctx, extraction):
		"""Best-effort exact match for header Link fields; real forms handle the rest."""
		meta = ctx.meta()
		for f in extraction.extracted_fields:
			df = meta.get_field(f.fieldname)
			if df and df.fieldtype == "Link" and df.options:
				printed = f.llm_value or f.value
				if printed and frappe.db.exists(df.options, printed):
					f.value = printed
					f.matched_value = printed
					f.match_method = "exact"
		return None

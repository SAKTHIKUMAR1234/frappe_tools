"""GenericPlugin — the first-class default for ANY DocType.

Runs the full pipeline with zero document-specific code: header scalar fields +
all rule-book-declared child tables, light generic Link resolution, build 1:1.
Specific plugins subclass this and override only what differs.
"""

import json

import frappe

from frappe_tools.extractors import schema as S
from frappe_tools.extractors import memory, validation
from frappe_tools.extractors.base import ExtractionPlugin


class GenericPlugin(ExtractionPlugin):
	def schema(self, ctx):
		return {
			"header": S.build_header_schema(ctx.target_doctype),
			"tables": S.build_tables_schema(ctx.target_doctype),
		}

	def resolve(self, ctx, extraction):
		"""Resolve configured Link fields and expose bounded candidate evidence."""
		meta = ctx.meta()
		profile = {row["fieldname"]: row for row in self.schema(ctx).get("header") or []}
		for f in extraction.extracted_fields:
			df = meta.get_field(f.fieldname)
			if not (df and df.fieldtype == "Link" and df.options):
				continue
			printed = f.llm_value or f.value
			if not printed:
				continue
			resolver = (profile.get(f.fieldname) or {}).get("resolver") or {}
			remembered = memory.resolve(extraction, f, (profile.get(f.fieldname) or {}).get("memory") or {}, df.options)
			if remembered:
				_set_match(f, remembered["value"], "reviewer-memory")
				f.candidates_json = json.dumps([{"value": remembered["value"], "exact": True, "source": "memory", **remembered}], ensure_ascii=False)
				continue
			resolver_type = resolver.get("type") or "None"
			if resolver_type == "None":
				if frappe.db.exists(df.options, printed):
					_set_match(f, printed, "exact-name")
				continue
			candidates = _link_candidates(df.options, printed, resolver, search=resolver_type == "Search")
			f.candidates_json = json.dumps(candidates, ensure_ascii=False)
			exact = [candidate for candidate in candidates if candidate.get("exact")]
			if len(exact) == 1:
				_set_match(f, exact[0]["value"], "configured-exact")
		return None

	def validate(self, ctx, extraction):
		return validation.validate(ctx.target_doctype, extraction)


def _set_match(row, value, method):
	row.value = value
	row.matched_value = value
	row.match_method = method


def _link_candidates(doctype, printed, resolver, search=False, limit=10):
	"""Permission-aware, bounded candidates. Fixed filters are config-owned."""
	meta = frappe.get_meta(doctype)
	configured = resolver.get("match_fields") or ["name", meta.get_title_field() or "name"]
	fields = []
	for fieldname in configured:
		if fieldname == "name" or meta.has_field(fieldname):
			if fieldname not in fields:
					fields.append(fieldname)
	if not fields:
		fields = ["name"]
	select_fields = ["name"] + [field for field in fields if field != "name"]
	fixed = resolver.get("filters") if isinstance(resolver.get("filters"), dict) else {}
	operator = "like" if search else "="
	value = f"%{printed}%" if search else printed
	or_filters = [[doctype, field, operator, value] for field in fields]
	rows = frappe.get_list(doctype, fields=select_fields, filters=fixed, or_filters=or_filters,
		limit_page_length=min(max(int(limit), 1), 20), order_by="modified desc")
	out = []
	needle = str(printed).strip().casefold()
	for row in rows:
		matched = [field for field in fields if str(row.get(field) or "").strip().casefold() == needle]
		label_field = meta.get_title_field() or "name"
		out.append({
			"value": row.name,
			"label": row.get(label_field) or row.name,
			"exact": bool(matched),
			"matched_fields": matched,
		})
	return out

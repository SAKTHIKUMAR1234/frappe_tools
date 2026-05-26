"""ExtractionPlugin — capability interface with safe generic defaults.

GenericPlugin and specific plugins inherit these; a specific plugin overrides only
what differs. The pipeline and the review-action endpoints call these methods and
never need to know which plugin they hold.
"""

import json

import frappe
from frappe.utils import cint


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

	# ----- review actions (generic defaults; plugins override) --------------
	def confirm_row(self, ctx, extraction, row_no, value):
		"""Confirm a line's matched record (a link value)."""
		line = get_line(extraction, row_no)
		line.matched_item = value
		line.resolution_status = "Confirmed"
		line.match_method = "user-confirmed"
		line.match_confidence = 1.0
		extraction.save()
		return {"ok": True, "value": value}

	def free_text_row(self, ctx, extraction, row_no):
		"""Mark a line as free-text (no master link)."""
		line = get_line(extraction, row_no)
		line.matched_item = None
		line.resolution_status = "Free Text"
		extraction.save()
		return {"ok": True}

	def create_row_master(self, ctx, extraction, row_no, opts):
		"""Create the master record a line maps to. Not supported by default."""
		frappe.throw(frappe._("Creating a master record from a line isn't supported for {0}.").format(ctx.target_doctype))

	def create_link_record(self, ctx, extraction, fieldname, opts):
		"""Generic: create the header Link field's target record from opts and set the field."""
		df = ctx.meta().get_field(fieldname)
		if not df or df.fieldtype != "Link" or not df.options:
			frappe.throw(frappe._("{0} is not a Link field.").format(fieldname))
		link_dt = df.options
		frappe.has_permission(link_dt, "create", throw=True)
		link_meta = frappe.get_meta(link_dt)
		rec = frappe.new_doc(link_dt)
		for k, v in (opts or {}).items():
			if v in (None, "") or not link_meta.has_field(k):
				continue
			rec.set(k, v)
		title = link_meta.get_title_field() or "name"
		if title != "name" and link_meta.has_field(title) and not rec.get(title):
			firstval = next((v for v in (opts or {}).values() if v), None)
			if firstval:
				rec.set(title, firstval)
		rec.insert()
		set_field_value(extraction, fieldname, rec.name)
		extraction.save()
		return {"ok": True, "value": rec.name}

	# ----- UI ---------------------------------------------------------------
	def provenance_map(self, extraction):
		"""{"fields": {fieldname: {bbox,page}}, "tables": {table: {row_no: {bbox,page}}}}."""
		return default_provenance_map(extraction)


# ----- shared helpers ------------------------------------------------------

def get_line(extraction, row_no):
	line = next((l for l in extraction.lines if cint(l.row_no) == cint(row_no)), None)
	if not line:
		frappe.throw(frappe._("Line {0} not found.").format(row_no))
	return line


def set_field_value(extraction, fieldname, value, method="user-created"):
	for f in extraction.extracted_fields:
		if f.fieldname == fieldname:
			f.value = value
			f.matched_value = value
			f.match_method = method
			f.status = "Edited"


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

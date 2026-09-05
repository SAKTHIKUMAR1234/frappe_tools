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

	def instructions(self, ctx):
		"""Code-owned prompt/rule blocks for this adapter."""
		return []

	def adapter_id(self):
		return f"{self.system}:{self.target_doctype}"

	def classification_description(self):
		"""Short visual description used by the automatic intake router."""
		return f"A document whose intended Frappe operation is {self.target_doctype}."

	def scanner_layouts(self):
		"""Return adapter-owned scanner layouts to materialize during setup.

		The runtime continues to use submitted ``Document Scanner Layout`` records
		so Link fields, the legacy scanner, and scan lineage all share one source of
		truth.  Consumer adapters may declare their defaults here instead of making
		the reusable core aware of private document types.
		"""
		return []

	def reference_arguments(self, extraction):
		"""Return the reusable dict passed into the code-owned reference adapter."""
		return {
			"fields": {row.fieldname: row.value for row in extraction.extracted_fields},
			"tables": [
				{
					"table": row.table or extraction.line_table,
					"row_no": row.row_no,
					"values": compact_decision_values(json.loads(row.raw_json or "{}")),
				}
				for row in extraction.lines
			],
		}

	def reference(self, extraction, arguments):
		"""Code adapter boundary: turn extracted arguments into local references."""
		return self.collect_references(extraction)

	def collect_references(self, extraction):
		from frappe_tools.automation.contracts import ReferenceBundle
		from frappe_tools.automation.learning import approved_memory, memory_scope_keys
		facts = {
			"fields": {row.fieldname: {"value": row.value, "printed": row.llm_value, "confidence": row.confidence} for row in extraction.extracted_fields},
			"tables": [{"table": row.table or extraction.line_table, "row_no": row.row_no,
				"values": compact_decision_values(json.loads(row.raw_json or "{}"))} for row in extraction.lines],
		}
		memories = approved_memory(self.adapter_id(), memory_scope_keys(extraction))
		return ReferenceBundle(self.adapter_id(), facts, {}, memories=memories)

	def decision_tools(self, extraction, bundle):
		return []

	def decision_model_name(self):
		return frappe.db.get_single_value("Document Extraction Settings", "decision_ai_model") or "Document Verification Sol"

	def decision_instructions(self):
		return (
			"Decide only from supplied facts, references and tool evidence. Never invent a Frappe record. "
			"Return one JSON object: status(review|handoff), confidence(0..1), values, reasons, "
			"handoff_reasons and memory_sources. Any ambiguity must be handoff."
		)

	def validate_decision(self, extraction, bundle, decision):
		return decision

	def apply_decision(self, extraction, decision):
		"""Apply a validated review decision to staging only; adapters opt in explicitly."""
		frappe.throw(frappe._("Decision application is not implemented for {0}.").format(self.target_doctype))

	# ----- pipeline hooks ---------------------------------------------------
	def resolve(self, ctx, extraction):
		"""Fill matched values + candidates on header fields and line rows (in place)."""
		return None

	def transform(self, ctx, extraction, build):
		"""Reshape the build dict {table: [child_row_dict, ...]} before insert."""
		return None

	def writer(self, ctx, extraction, build):
		"""Code writer boundary; defaults to the existing adapter transform hook."""
		return self.transform(ctx, extraction, build)

	def target_for_build(self, ctx, extraction):
		"""Return the target document to populate.

		Adapters may explicitly opt into updating a staging document that they own.
		The safe default remains creation of a new draft.
		"""
		return frappe.new_doc(self.target_doctype)

	def processes_attached_target(self):
		"""Whether Attach Existing should run extraction before adapter-owned update."""
		return False

	def customize(self, ctx, doc, extraction):
		"""Adjust the freshly-built, unsaved target document (app-aware)."""
		return None

	def after_insert(self, ctx, doc, extraction):
		"""Run adapter-owned post-insert linking after child rows have permanent names."""
		return None

	def validate(self, ctx, extraction):
		"""Return a list of human-readable issues blocking creation, or []."""
		return []

	def validator(self, ctx, extraction):
		"""Code validator boundary used immediately before the writer."""
		return self.validate(ctx, extraction)

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
	def rulebook_defaults(self, ctx):
		"""Optional seed values; existing site rule books remain authoritative."""
		return []

	def review_phases(self, ctx):
		"""Ordered handoffs: key, sections, automation, human_input and callbacks.

		Optional automate/validate/complete callbacks receive (ctx, extraction).
		Completed inputs are checkpointed by the server; edits reopen that phase
		and its successors without repeating source extraction.
		"""
		return []

	def setup(self, ctx):
		"""Idempotent adapter-owned site setup, called after install/migrate.

		Seed missing configuration here; preserve administrator edits and roles.
		"""
		return None

	def review_actions(self, ctx):
		"""Named UI actions: {name: {handler, permissions: [(doctype, ptype)]}}.

		Handlers receive (ctx, extraction, values). Only installed Python code can
		register a handler; browsers send the action name, never a method path.
		"""
		return {}

	def link_queries(self, ctx, extraction):
		"""Named Link controls: {key: {doctype, fields, search_fields, filters}}.

		Fields control the record preview. Filters may depend on saved review
		values. All searches and previews enforce the current user's permissions.
		"""
		return {}

	def workflow(self, ctx):
		"""Public presentation contract. Override in the same file as the adapter.

		Sections reference schema keys; they cannot expose new fields or methods.
		The server remains responsible for permissions and the final action gate.
		"""
		return {
			"label": self.target_doctype,
			"description": f"Extract and review a {self.target_doctype} from scanned pages.",
			"icon": "pi pi-file",
			"review_sections": [],
			"action_label": f"Create {self.target_doctype} draft",
			"action_description": f"Save the reviewed values as a {self.target_doctype} draft.",
			"result_description": "Open the saved document to continue its normal workflow.",
		}

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


def compact_decision_values(values):
	"""Keep decision payloads bounded: scalar facts plus evidence state, never full coordinates/audit blobs."""
	values = dict(values or {})
	evidence = values.pop("_cell_evidence", {}) if isinstance(values.get("_cell_evidence"), dict) else {}
	for key in ("page", "bbox", "_invoice_candidates", "resolved_sales_invoices"):
		values.pop(key, None)
	if evidence:
		values["evidence"] = {key: {"confidence": item.get("confidence"),
			"verified": bool(item.get("verified")), "source": item.get("source") or "model"}
			for key, item in evidence.items() if isinstance(item, dict) and item.get("raw_text")}
	return values

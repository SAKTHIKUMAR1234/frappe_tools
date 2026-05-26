"""ERPNext Purchase Invoice plugin — thin composition of the concern modules.

Subclasses GenericPlugin (so the general pipeline handles everything not overridden
here) and wires the PI-specific capabilities: curated schema, supplier+item
resolution, line consolidation, India-Compliance tax handling, and the review
actions (confirm / create Item / create Supplier).
"""

from frappe import _

from frappe_tools.extractors import register
from frappe_tools.extractors.erpnext.purchase_invoice import actions as actions_mod
from frappe_tools.extractors.erpnext.purchase_invoice import resolve as resolve_mod
from frappe_tools.extractors.erpnext.purchase_invoice import schema as schema_mod
from frappe_tools.extractors.erpnext.purchase_invoice import taxes as taxes_mod
from frappe_tools.extractors.erpnext.purchase_invoice import transform as transform_mod
from frappe_tools.extractors.generic.plugin import GenericPlugin


@register
class PurchaseInvoicePlugin(GenericPlugin):
	system = "ERPNext"
	target_doctype = "Purchase Invoice"

	# ----- extraction shape -------------------------------------------------
	def schema(self, ctx):
		return {"header": schema_mod.HEADER, "tables": schema_mod.TABLES}

	def prompt_addendum(self, ctx):
		return schema_mod.PROMPT_ADDENDUM

	# ----- pipeline hooks ---------------------------------------------------
	def resolve(self, ctx, extraction):
		resolve_mod.resolve(ctx, extraction)

	def transform(self, ctx, extraction, build):
		transform_mod.transform(ctx, extraction, build)

	def customize(self, ctx, doc, extraction):
		taxes_mod.customize(ctx, doc, extraction)

	# ----- review actions ---------------------------------------------------
	def confirm_row(self, ctx, extraction, row_no, value):
		return actions_mod.confirm_row(extraction, row_no, value)

	def create_row_master(self, ctx, extraction, row_no, opts):
		return actions_mod.create_row_master(extraction, row_no, opts)

	def create_link_record(self, ctx, extraction, fieldname, opts):
		if fieldname == "supplier":
			return actions_mod.create_supplier(extraction, opts)
		return super().create_link_record(ctx, extraction, fieldname, opts)

	# ----- validation / review policy --------------------------------------
	def review_policy(self):
		return {"supplier": "confirm", "bill_no": "confirm"}

	def validate(self, ctx, extraction):
		issues = []
		if not actions_mod.get_resolved_supplier(extraction):
			issues.append(_("Resolve the supplier to an existing record before creating."))
		has_items = any(
			(l.table or extraction.line_table) == "items" and l.resolution_status != "Rejected"
			for l in extraction.lines
		)
		if not has_items:
			issues.append(_("There are no line items to post."))
		return issues

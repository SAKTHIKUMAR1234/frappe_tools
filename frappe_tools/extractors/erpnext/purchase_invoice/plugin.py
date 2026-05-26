"""ERPNext Purchase Invoice plugin — thin composition of the concern modules.

Subclasses GenericPlugin (so the general pipeline handles everything not overridden
here) and wires the PI-specific capabilities: curated schema, supplier+item
resolution, line consolidation, and India-Compliance tax handling.
"""

from frappe import _

from frappe_tools.api import doc_resolve
from frappe_tools.extractors import register
from frappe_tools.extractors.erpnext.purchase_invoice import resolve as resolve_mod
from frappe_tools.extractors.erpnext.purchase_invoice import schema as schema_mod
from frappe_tools.extractors.erpnext.purchase_invoice import taxes as taxes_mod
from frappe_tools.extractors.erpnext.purchase_invoice import transform as transform_mod
from frappe_tools.extractors.generic.plugin import GenericPlugin


@register
class PurchaseInvoicePlugin(GenericPlugin):
	system = "ERPNext"
	target_doctype = "Purchase Invoice"

	def schema(self, ctx):
		return {"header": schema_mod.HEADER, "tables": schema_mod.TABLES}

	def prompt_addendum(self, ctx):
		return schema_mod.PROMPT_ADDENDUM

	def resolve(self, ctx, extraction):
		resolve_mod.resolve(ctx, extraction)

	def transform(self, ctx, extraction, build):
		transform_mod.transform(ctx, extraction, build)

	def customize(self, ctx, doc, extraction):
		taxes_mod.customize(ctx, doc, extraction)

	def review_policy(self):
		return {"supplier": "confirm", "bill_no": "confirm"}

	def validate(self, ctx, extraction):
		issues = []
		if not doc_resolve.get_resolved_supplier(extraction):
			issues.append(_("Resolve the supplier to an existing record before creating."))
		has_items = any(
			(l.table or extraction.line_table) == "items" and l.resolution_status != "Rejected"
			for l in extraction.lines
		)
		if not has_items:
			issues.append(_("There are no line items to post."))
		return issues

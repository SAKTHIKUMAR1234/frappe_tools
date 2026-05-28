"""ESSDEE Return Goods plugin — shapes extraction of a customer's RETURN invoice.

This only defines WHAT to extract from the scanned customer invoice (header +
line columns). Matching each line to our essdee_sales Item Variant and comparing
against the Return Goods is done in essdee_sales (which knows the specific Return
Goods, its customer, and candidate variants) — so resolve() is intentionally a
no-op here.
"""

from frappe_tools.extractors import register
from frappe_tools.extractors.generic.plugin import GenericPlugin

HEADER = [
    {"fieldname": "invoice_no", "label": "Customer Invoice No", "fieldtype": "Data",
     "description": "The customer's own invoice/document number printed on the return invoice."},
    {"fieldname": "invoice_date", "label": "Invoice Date", "fieldtype": "Date",
     "description": "Date printed on the customer's return invoice."},
]

LINE_COLUMNS = [
    {"key": "description", "label": "Description", "type": "text",
     "description": "Item description exactly as the customer printed it (names may differ from ours)."},
    {"key": "qty", "label": "Qty", "type": "number", "description": "Quantity for the line as printed."},
    {"key": "uom", "label": "UOM", "type": "text",
     "description": "Unit such as Box/Pcs/Units if printed; blank if not shown."},
    {"key": "rate", "label": "Rate", "type": "number", "description": "Unit price as printed."},
    {"key": "amount", "label": "Amount", "type": "number", "description": "Line amount (qty x rate)."},
]

TABLES = [{"table": "lines", "label": "Invoice Lines", "columns": LINE_COLUMNS}]

PROMPT_ADDENDUM = (
    "This is a CUSTOMER's return invoice for goods being returned to us. Capture each line's "
    "description EXACTLY as the customer wrote it (their wording often differs from ours), with "
    "qty, uom and rate. Ignore tax/summary/total rows (CGST/SGST/IGST, round-off, grand total)."
)


@register
class ReturnGoodsCustomerInvoicePlugin(GenericPlugin):
    system = "ERPNext"
    target_doctype = "Return Goods"

    def schema(self, ctx):
        return {"header": HEADER, "tables": TABLES}

    def prompt_addendum(self, ctx):
        return PROMPT_ADDENDUM

    def resolve(self, ctx, extraction):
        # Variant matching + verification is RG-aware and lives in essdee_sales.
        return None

    def validate(self, ctx, extraction):
        return []

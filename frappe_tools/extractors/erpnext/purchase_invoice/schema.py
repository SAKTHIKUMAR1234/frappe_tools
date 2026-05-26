"""What to extract from a supplier invoice — curated header fields + items columns."""

HEADER = [
	{"fieldname": "supplier", "label": "Supplier", "fieldtype": "Link", "link_doctype": "Supplier",
	 "required": True, "description": "Supplier / vendor name exactly as printed (the issuer, not the buyer)."},
	{"fieldname": "bill_no", "label": "Supplier Invoice No", "fieldtype": "Data", "required": True,
	 "description": "The supplier's own invoice/bill number printed on the document."},
	{"fieldname": "bill_date", "label": "Supplier Invoice Date", "fieldtype": "Date",
	 "description": "Date printed on the supplier invoice."},
	{"fieldname": "posting_date", "label": "Posting Date", "fieldtype": "Date",
	 "description": "Use the invoice date unless a distinct received/posting date is shown."},
	{"fieldname": "supplier_gstin", "label": "Supplier GSTIN", "fieldtype": "Data",
	 "description": "Supplier's 15-character GSTIN if printed."},
]

LINE_COLUMNS = [
	{"key": "description", "label": "Description", "type": "text",
	 "description": "Item/service description exactly as printed for the line."},
	{"key": "supplier_code", "label": "Supplier Item Code", "type": "text",
	 "description": "Any item/SKU/part code printed beside the line (not the HSN)."},
	{"key": "hsn", "label": "HSN/SAC", "type": "text", "description": "HSN or SAC code if printed."},
	{"key": "qty", "label": "Qty", "type": "number"},
	{"key": "uom", "label": "UOM", "type": "text", "description": "Unit such as Nos, Kg, Mtr; blank if not shown."},
	{"key": "rate", "label": "Rate", "type": "number", "description": "Unit price before tax."},
	{"key": "amount", "label": "Amount", "type": "number", "description": "Line amount before tax (qty x rate)."},
]

TABLES = [{"table": "items", "label": "Items", "columns": LINE_COLUMNS}]

PROMPT_ADDENDUM = (
	"This is a SUPPLIER (purchase) invoice recorded as a Purchase Invoice. Capture the "
	"supplier's identity (name + GSTIN) and every line item with the supplier's exact wording "
	"and any code printed beside it. Do not compute taxes; report printed rate/amount as-is. "
	"Treat charges/services (e.g. 'dyeing charges') as line items. Ignore tax/summary rows "
	"(CGST/SGST/IGST, round-off, totals)."
)

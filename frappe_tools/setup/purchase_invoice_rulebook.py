import frappe

TITLE = "Purchase Invoice — Supplier Invoice"

INSTRUCTIONS = """This is a supplier's PURCHASE / TAX INVOICE that we are recording as an ERPNext Purchase Invoice.

WHO IS WHO (critical):
- The SUPPLIER is the SELLER who issued the invoice — look for 'From', 'Sold By', 'Billed By', or the party whose name + GSTIN appear at the top as the issuer. Do NOT use the buyer / consignee / ship-to / 'Bill To' party (that is our own company).
- supplier_gstin is the SELLER's 15-character GSTIN.

HEADER:
- bill_no  = the supplier's OWN invoice number (labels: Invoice No, Bill No, Tax Invoice No, Inv #).
- bill_date = the date the supplier printed on the invoice.
- posting_date = same as bill_date unless a distinct received / entry date is shown.

LINE ITEMS (one object per printed row in the items / particulars table):
- description = the item or service text EXACTLY as printed — keep the supplier's wording verbatim; do not translate, expand, or normalise it.
- supplier_code = any item / SKU / part / catalogue code printed beside the line (this is NOT the HSN/SAC).
- hsn = the HSN or SAC code if printed.
- qty, uom, rate (unit price before tax), amount (line total before tax).
- Treat charges and services (e.g. 'dyeing charges', 'freight', 'labour', 'job work') as line items too.
- IGNORE tax/summary rows — CGST/SGST/IGST lines, round-off, sub-total, grand total are NOT line items.

GENERAL:
- Numbers: digits only, no currency symbols or thousand separators. Dates: YYYY-MM-DD.
- If a value is not present on the document, return null.
"""

FIELD_RULES = [
	{"fieldname": "supplier", "label": "Supplier", "required": 1,
	 "instruction": "The seller/vendor that issued the invoice (From / Billed By), never the buyer or ship-to party."},
	{"fieldname": "bill_no", "label": "Supplier Invoice No", "required": 1,
	 "instruction": "The supplier's own printed invoice/bill number.", "example": "INV-2024-00123"},
	{"fieldname": "bill_date", "label": "Supplier Invoice Date",
	 "instruction": "Date printed on the supplier invoice.", "output_format": "YYYY-MM-DD"},
	{"fieldname": "supplier_gstin", "label": "Supplier GSTIN",
	 "instruction": "The seller's 15-character GSTIN (begins with a 2-digit state code).", "example": "33ABCDE1234F1Z5"},
]


def create_purchase_invoice_rulebook(force=False):
	"""Create the default Purchase Invoice rule book if none exists. Idempotent."""
	existing = frappe.get_all("Document Rule Book", filters={"target_doctype": "Purchase Invoice"}, pluck="name")
	if existing and not force:
		return f"exists: {existing[0]}"

	doc = frappe.new_doc("Document Rule Book")
	doc.title = TITLE
	doc.target_doctype = "Purchase Invoice"
	doc.enabled = 1
	doc.priority = 10
	doc.line_table = "items"
	doc.description = "Extract an Indian GST supplier invoice into a Purchase Invoice (header + line items)."
	doc.instructions = INSTRUCTIONS
	for r in FIELD_RULES:
		doc.append("field_rules", r)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

FIELDS = [
	{"fieldname": "ai_data_section", "fieldtype": "Section Break", "label": "AI Data", "collapsible": 1,
		"insert_after": "uploaded_to_google_drive"},
	{"fieldname": "ai_page_data", "fieldtype": "JSON", "label": "AI Page Data", "insert_after": "ai_data_section"},
	{"fieldname": "ai_page_count", "fieldtype": "Int", "label": "AI Page Count", "insert_after": "ai_page_data"},
	{"fieldname": "ai_page_data_on", "fieldtype": "Datetime", "label": "AI Page Data On",
		"insert_after": "ai_page_count"},
	{"fieldname": "ai_page_model", "fieldtype": "Data", "label": "AI Page Model", "insert_after": "ai_page_data_on"},
	{"fieldname": "ai_page_error", "fieldtype": "Small Text", "label": "AI Page Error",
		"insert_after": "ai_page_model"},
	{"fieldname": "ai_page_error_on", "fieldtype": "Datetime", "label": "AI Page Error On",
		"insert_after": "ai_page_error"},
	{"fieldname": "ai_page_attempts", "fieldtype": "Int", "label": "AI Page Attempts", "default": "0",
		"insert_after": "ai_page_error_on"},
]


def create_file_fields() -> None:
	"""Create the read-only AI page data fields on File."""
	create_custom_fields({"File": [{**field, "read_only": 1} for field in FIELDS]}, update=True)

# Copyright (c) 2026, sakthi123msd@gmail.com and contributors
# For license information, please see license.txt

import frappe


def execute(filters=None):
	if not filters.get('document_type'):
		frappe.throw("Please select the document type needs to be selected")
	columns, data = get_columns(filters), get_data(filters)
	return columns, data

def get_data(filters = {}):
	if not filters:
		frappe.throw("Please Setup Filters")

	document_type = filters.get('document_type')
	filter_field = filters.get('filter_field')
	if not filter_field:
		frappe.throw("Please select the field to filter by")

	field_conf = get_filter_field_config(document_type, filter_field)

	if field_conf.get('type') == 'Datetime':
		start = filters.get('datetime_start')
		end = filters.get('datetime_end')
	else:
		start = filters.get('date_start')
		end = filters.get('date_end')

	if not start or not end:
		frappe.throw("Please set the start and end range for the selected field")

	data = frappe.db.sql(f"""
		SELECT
			t1._doctype as document_type, t1._docname as docname,
			t1.scanner_layout as layout,
			COUNT(t2.name) as page_count,
			t2.title,
			t3.docstatus, t1.name as scan_name, t1.owner as created_by, t1.creation, t1.modified
		FROM `tabScanned Document` t1
		JOIN `tabScanned Document Detail` t2 ON t1.name = t2.scanner_document
		JOIN `tab{document_type}` t3 ON t3.name = t1._docname
		WHERE t2.is_deleted != 1
		AND t3.`{filter_field}` BETWEEN {frappe.db.escape(start)} AND {frappe.db.escape(end)}
		GROUP BY t2.title , t1.name, t1._docname, t1._doctype
		ORDER BY t3.`{filter_field}` DESC
	""", as_dict=True)

	return data

@frappe.whitelist()
def get_report_date_fields(document_type):
	"""Return the list of date/datetime fields configured for this document type.

	The configuration is stored as JSON on Document Scanner Settings Items. Each entry is a
	dict: {"field": <fieldname>, "type": "Date"|"Datetime", "default": <optional default>}.
	``type`` controls how the report filter is rendered and may intentionally differ from the
	column's real type (e.g. filter a Datetime column as a Date). Falls back to a single
	``creation`` (Datetime) field when nothing is configured. Field names are validated against
	the doctype so they are safe to interpolate directly into the report query.
	"""
	raw = frappe.db.get_value(
		"Document Scanner Settings Items",
		{"doctype_link": document_type},
		"report_date_fields",
	)

	configured = []
	if raw:
		try:
			configured = frappe.parse_json(raw) or []
		except Exception:
			frappe.throw(f"Report Date Fields for {document_type} is not valid JSON")

	if not isinstance(configured, list):
		frappe.throw(f"Report Date Fields for {document_type} must be a JSON list")

	if not configured:
		configured = [{"field": "creation", "type": "Datetime"}]

	meta = frappe.get_meta(document_type)
	cleaned = []
	for entry in configured:
		if not isinstance(entry, dict):
			continue
		fieldname = (entry.get("field") or "").strip()
		if not fieldname:
			continue
		if fieldname not in ("creation", "modified") and not meta.has_field(fieldname):
			frappe.throw(f"Field '{fieldname}' in Report Date Fields does not exist on {document_type}")
		field_type = (entry.get("type") or "Date").strip().title()
		if field_type not in ("Date", "Datetime"):
			field_type = "Date"
		cleaned_entry = {"field": fieldname, "type": field_type, "label": resolve_field_label(meta, fieldname, entry.get("label"))}
		if entry.get("default") not in (None, ""):
			cleaned_entry["default"] = entry.get("default")
		cleaned.append(cleaned_entry)

	return cleaned

STD_FIELD_LABELS = {"creation": "Created On", "modified": "Last Updated On", "name": "ID"}

def resolve_field_label(meta, fieldname, configured_label=None):
	"""Pick the label to show in the report's field dropdown: explicit config label first,
	then the doctype's own field label, then a friendly fallback derived from the fieldname."""
	label = (configured_label or "").strip()
	if label:
		return label
	df = meta.get_field(fieldname)
	if df and df.label:
		return df.label
	return STD_FIELD_LABELS.get(fieldname) or fieldname.replace("_", " ").title()

def get_filter_field_config(document_type, filter_field):
	for entry in get_report_date_fields(document_type):
		if entry["field"] == filter_field:
			return entry
	frappe.throw(f"Selected field '{filter_field}' is not configured in Report Date Fields for {document_type}")

def get_columns(filters):
	return [
		{
			"label": "Document Name",
			"fieldname": "docname",
			"fieldtype": "Link",
			"options": filters.get("document_type"),
			"width": 180
		},
		{
			"label": "Page Title",
			"fieldname": "title",
			"fieldtype": "Data",
			"width": 200
		},
		{
			"label": "Page Layout",
			"fieldname": "layout",
			"fieldtype": "Data",
			"width": 120
		},
		{
			"label": "Page Count",
			"fieldname": "page_count",
			"fieldtype": "Int",
			"width": 110
		},
		{
			"label": "Document Status",
			"fieldname": "docstatus",
			"fieldtype": "Int",
			"width": 100
		},
		{
			"label": "Scanned Document Name",
			"fieldname": "scan_name",
			"fieldtype": "Data",
			"width": 180
		},
		{
			"label": "Created By",
			"fieldname": "created_by",
			"fieldtype": "Link",
			"options": "User",
			"width": 150
		},
		{
			"label": "Created On",
			"fieldname": "creation",
			"fieldtype": "Datetime",
			"width": 160
		},
		{
			"label": "Last Modified",
			"fieldname": "modified",
			"fieldtype": "Datetime",
			"width": 160
		}
	]


@frappe.whitelist()
def get_document_types():
	return frappe.get_all("Document Scanner Settings Items", pluck='doctype_link')

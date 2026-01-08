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

	data = frappe.db.sql(f"""
		SELECT 
			   t1._doctype as document_type, t1._docname as docname,
			   t1.scanner_layout as layout,
			   COUNT(t2.name) as page_count,
			   t2.title,
			   t3.docstatus, t1.name as scan_name, t1.owner as created_by, t1.creation, t1.modified
		FROM `tabScanned Document` t1
		JOIN `tabScanned Document Detail` t2 ON t1.name = t2.scanner_document
		JOIN `tab{filters.get('document_type')}` t3 ON t3.name = t1._docname
		WHERE t2.is_deleted != 1 
		AND t3.creation BETWEEN {frappe.db.escape(filters.get('start_date'))} AND  {frappe.db.escape(filters.get('end_date'))}
		GROUP BY t2.title , t1.name, t1._docname, t1._doctype
	""", as_dict=True)
	
	return data

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
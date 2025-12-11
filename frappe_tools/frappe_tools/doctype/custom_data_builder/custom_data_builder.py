# Copyright (c) 2025, sakthi123msd@gmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CustomDataBuilder(Document):
	pass

@frappe.whitelist()
def get_document_uploaded_values(doc_name):
    file = frappe.db.get_value("Custom Data Builder", doc_name, 'resource_document')
    frappe.log(file)

@frappe.whitelist()
def get_list_details(doctype, filters=None, limit=10):
    filters = frappe.json.loads(filters or "[]")

    data = frappe.get_all(
        doctype,
        filters=filters,
        limit=limit,
        fields="*"
    )

    total_count = frappe.db.count(doctype, filters)

    return {
        "data": data,
        "total_count": total_count
    }


@frappe.whitelist()
def download_excel(name):
    from openpyxl import Workbook
    doc = frappe.get_doc("Data Builder", name)

    if doc.resource_document_type == "Doctype":
        filters = frappe.parse_json(doc.filter_json or "[]")

        data = frappe.get_all(
            doc._doctype,
            filters=filters,
            fields="*",
        )
    else:
        data = frappe.parse_json(doc.extracted_data_json or "[]")

    if not data:
        frappe.throw("No data available to export.")

    wb = Workbook()
    ws = wb.active
    ws.title = "Data"

    headers = list(data[0].keys())
    ws.append(headers)

    for row in data:
        ws.append([row.get(h, "") for h in headers])

    file_name = f"{doc.name}.xlsx"
    file_path = frappe.get_site_path("private", "files", file_name)
    wb.save(file_path)

    frappe.local.response.filecontent = open(file_path, "rb").read()
    frappe.local.response.type = "download"
    frappe.local.response.filename = file_name

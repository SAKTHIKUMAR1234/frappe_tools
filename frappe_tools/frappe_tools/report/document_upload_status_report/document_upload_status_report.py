import frappe

from frappe_tools.frappe_tools.report.scanned_document_detail_report.scanned_document_detail_report import (
	get_filter_field_config,
)


def execute(filters=None):
    filters = filters or {}

    doctype = filters.get("document_type")
    if not doctype:
        frappe.throw("DocType is required")

    filter_field = filters.get("filter_field")
    if not filter_field:
        frappe.throw("Please select the field to filter by")

    field_conf = get_filter_field_config(doctype, filter_field)

    if field_conf.get("type") == "Datetime":
        from_date = filters.get("datetime_start")
        to_date = filters.get("datetime_end")
    else:
        from_date = filters.get("date_start")
        to_date = filters.get("date_end")

    if not from_date or not to_date:
        frappe.throw("Please set the start and end range for the selected field")

    meta = frappe.get_meta(doctype)

    listview_fields = get_listview_fields(meta)

    columns = []
    for fieldname in listview_fields:
        df = meta.get_field(fieldname)

        columns.append({
            "label": df.label if df else fieldname.replace("_", " ").title(),
            "fieldname": fieldname,
            "fieldtype": df.fieldtype if df else "Data",
            "width": 150,
        })

    columns.append({
        "label": "Scan Status",
        "fieldname": "is_scanned",
        "fieldtype": "Data",
        "width": 200,
    })

    query_filters = {"docstatus": ["<", 2]}
    query_filters[filter_field] = ["between", [from_date, to_date]]

    data = frappe.get_all(
        doctype,
        filters=query_filters,
        fields=listview_fields,
        order_by="modified desc",
    )

    docnames = [d.name for d in data]

    scanned = frappe.get_all(
        "Scanned Document",
        filters={
            "_doctype": doctype,
            "_docname": ["in", docnames],
        },
        pluck="_docname",
    )

    scanned_set = set(scanned)

    for row in data:
        row["is_scanned"] = 1 if row.name in scanned_set else 0

    scan_status = filters.get("scan_status")
    if scan_status == "Scanned":
        data = [row for row in data if row.is_scanned]
    elif scan_status == "Not Scanned":
        data = [row for row in data if not row.is_scanned]

    return columns, data

def get_listview_fields(meta):
    fields = []

    if meta.title_field:
        fields.append(meta.title_field)

    for df in meta.fields:
        if df.in_list_view and df.fieldname not in fields:
            fields.append(df.fieldname)

    if "name" not in fields:
        fields.insert(0, "name")

    if "modified" not in fields:
        fields.append("modified")

    return fields

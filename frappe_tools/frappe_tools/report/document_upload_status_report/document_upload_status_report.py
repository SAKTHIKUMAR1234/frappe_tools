import frappe

def execute(filters=None):
    filters = filters or {}

    doctype = filters.get("document_type")
    if not doctype:
        frappe.throw("DocType is required")

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
        "label": "Scanned",
        "fieldname": "is_scanned",
        "fieldtype": "Check",
        "width": 90,
    })

    query_filters = {"docstatus": ["<", 2]}

    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    date_field = 'creation'

    if from_date and to_date:
        query_filters[date_field] = ["between", [from_date, to_date]]

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
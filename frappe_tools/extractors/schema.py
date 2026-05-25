"""Generic schema builders — header fields + child tables from DocType meta + rule books.

Used by GenericPlugin (and reusable by specific plugins that only want to curate).
"""

import frappe

EXTRACTABLE_FIELDTYPES = {
	"Data", "Small Text", "Text", "Long Text", "Text Editor",
	"Select", "Link", "Dynamic Link",
	"Date", "Datetime", "Time",
	"Int", "Float", "Currency", "Percent",
	"Check", "Phone",
}
LINK_FIELDTYPES = {"Link", "Dynamic Link"}


def build_header_schema(target_doctype):
	meta = frappe.get_meta(target_doctype)
	schema = []
	for df in meta.fields:
		if df.fieldtype not in EXTRACTABLE_FIELDTYPES:
			continue
		if df.hidden or df.read_only or getattr(df, "is_virtual", 0):
			continue
		if df.fieldname.startswith("_"):
			continue
		entry = {"fieldname": df.fieldname, "label": df.label or df.fieldname, "fieldtype": df.fieldtype, "required": bool(df.reqd)}
		if df.fieldtype == "Select" and df.options:
			entry["options"] = [o for o in (df.options or "").split("\n") if o != ""]
		elif df.fieldtype == "Link" and df.options:
			entry["link_doctype"] = df.options
		if df.description:
			entry["description"] = df.description
		schema.append(entry)
	return schema


def rulebook_tables(target_doctype):
	"""Child table fieldnames declared across the enabled rule books (+ legacy line_table)."""
	out, seen = [], set()
	for name in frappe.get_all("Document Rule Book", filters={"target_doctype": target_doctype, "enabled": 1},
	                           order_by="priority desc, modified asc", pluck="name"):
		rb = frappe.get_doc("Document Rule Book", name)
		for t in (rb.tables or []):
			if t.table_fieldname and t.table_fieldname not in seen:
				seen.add(t.table_fieldname)
				out.append({"table": t.table_fieldname, "label": t.label, "notes": t.notes})
		if rb.line_table and rb.line_table not in seen:
			seen.add(rb.line_table)
			out.append({"table": rb.line_table, "label": None, "notes": None})
	return out


def table_columns_from_meta(target_doctype, table_fieldname, limit=20):
	meta = frappe.get_meta(target_doctype)
	cf = meta.get_field(table_fieldname)
	if not cf or cf.fieldtype != "Table":
		return []
	cm = frappe.get_meta(cf.options)
	cols = []
	for df in cm.fields:
		if df.fieldtype not in EXTRACTABLE_FIELDTYPES:
			continue
		if df.hidden or df.read_only or getattr(df, "is_virtual", 0):
			continue
		if df.fieldname.startswith("_"):
			continue
		col = {"key": df.fieldname, "label": df.label or df.fieldname, "type": df.fieldtype}
		if df.fieldtype == "Link" and df.options:
			col["link_doctype"] = df.options
		cols.append(col)
	return cols[:limit]


def build_tables_schema(target_doctype):
	"""[{table, label, notes?, columns:[...]}] from the rule books, columns from child meta."""
	spec = []
	for t in rulebook_tables(target_doctype):
		spec.append({"table": t["table"], "label": t.get("label"), "notes": t.get("notes"),
		             "columns": table_columns_from_meta(target_doctype, t["table"])})
	return spec


def primary_link_field(child_meta):
	for df in child_meta.fields:
		if df.fieldtype == "Link" and not df.hidden and not getattr(df, "is_virtual", 0):
			return df.fieldname
	return None


def get_rule_books(target_doctype):
	names = frappe.get_all("Document Rule Book", filters={"target_doctype": target_doctype, "enabled": 1},
	                       order_by="priority desc, modified asc", pluck="name")
	books = []
	for name in names:
		doc = frappe.get_doc("Document Rule Book", name)
		books.append({
			"title": doc.title,
			"instructions": doc.instructions,
			"field_rules": [
				{"fieldname": r.fieldname, "label": r.label, "instruction": r.instruction,
				 "example": r.example, "output_format": r.output_format, "required": bool(r.required)}
				for r in (doc.field_rules or [])
			],
		})
	return books

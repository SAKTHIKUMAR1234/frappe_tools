"""Read-only Frappe tools for AI agents, run as the session user with permission checks."""

import re

import frappe
from frappe.model import default_fields, no_value_fields, table_fields

from frappe_tools.ai.agent import Tool
from frappe_tools.file_data.documents import get_document_pages

MAX_LIST_ROWS = 50
MAX_PAGES = 20
COLUMNS = frozenset(default_fields) - {"doctype"}
DENIED_DOCTYPES = frozenset(
	{
		"AI Provider Account",
		"Document Extraction Settings",
		"User",
		"User Social Login",
		"Sessions",
		"Session Default Settings",
		"Integration Request",
		"Webhook",
		"Connected App",
		"Token Cache",
		"Social Login Key",
		"Email Account",
		"AWS S3 Settings",
		"S3 Backup Settings",
		"System Settings",
		"Version",
		"Document Share Key",
	}
)
DENIED_PREFIXES = ("OAuth", "WebAuthn")
DENIED_SUFFIXES = (" Log",)  # Logs can hold request payloads and errors with secrets.
SENSITIVE_FIELD = re.compile(r"key|secret|token|password", re.IGNORECASE)
OPERATORS = frozenset({"=", "!=", ">", "<", ">=", "<=", "like", "not like", "in", "not in", "between", "is"})
ORDER_BY = re.compile(r"(\w+)(?: (asc|desc))?", re.IGNORECASE)
SCALARS = (str, int, float, bool, type(None))


class ToolError(ValueError):
	"""A tool call that the model can correct."""


def is_denied_doctype(meta) -> bool:
	"""Deny credential, log, settings and session DocTypes, and any DocType with a Password field."""
	name = meta.name
	if name in DENIED_DOCTYPES or name.startswith(DENIED_PREFIXES) or name.endswith(DENIED_SUFFIXES):
		return True
	if meta.issingle or meta.is_virtual:
		return True
	return any(field.fieldtype == "Password" for field in meta.fields)


def get_readable_meta(doctype, allow_child: bool = False):
	if not isinstance(doctype, str) or not frappe.db.exists("DocType", doctype):
		raise ToolError(f"Unknown DocType {doctype}")
	meta = frappe.get_meta(doctype)
	if is_denied_doctype(meta) or (meta.istable and not allow_child):
		raise ToolError(f"DocType {doctype} is not available to AI tools")
	return meta


def is_sensitive_field(fieldname: str) -> bool:
	return bool(SENSITIVE_FIELD.search(fieldname))


def get_readable_columns(meta) -> set[str]:
	"""Return the columns the user may read, without sensitive names."""
	permitted = set(meta.get_permitted_fieldnames(permission_type="read")) | COLUMNS
	return {fieldname for fieldname in permitted if not is_sensitive_field(fieldname)}


def clean_record(record: dict, meta) -> dict:
	"""Drop Password and sensitive-name values and internal keys; recurse into child tables."""
	result = {}
	for key, value in record.items():
		field = meta.get_field(key)
		if key.startswith("__") or is_sensitive_field(key) or (field and field.fieldtype == "Password"):
			continue
		if field and field.fieldtype in table_fields:
			child = frappe.get_meta(field.options)
			value = [clean_record(row, child) for row in value or []]
		result[key] = value
	return result


def get_document(doctype: str, name: str, fields: list[str] | None = None) -> dict:
	meta = get_readable_meta(doctype)
	if not isinstance(name, str):
		raise ToolError("name must be text")
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	doc.apply_fieldlevel_read_permissions()
	data = clean_record(doc.as_dict(), meta)
	if fields:
		_check_fieldnames(meta, fields)
		data = {key: data[key] for key in ("name", *fields) if key in data}
	return {**data, "_links": get_link_targets(meta, data)}


def get_link_targets(meta, data: dict) -> dict[str, str]:
	"""Map each filled Link field, also "table.field" in child rows, to the DocType it points to."""
	targets = {}
	for field in meta.fields:
		value = data.get(field.fieldname)
		if field.fieldtype == "Link" and value:
			targets[field.fieldname] = field.options
		elif field.fieldtype == "Dynamic Link" and value:
			targets[field.fieldname] = data.get(field.options)
		elif field.fieldtype in table_fields and value:
			child = frappe.get_meta(field.options)
			for row in value:
				targets.update({f"{field.fieldname}.{key}": target for key, target in get_link_targets(child, row).items()})
	return targets


def get_list(
	doctype: str, filters=None, fields: list[str] | None = None, order_by: str | None = None, limit: int = 20
) -> list[dict]:
	meta = get_readable_meta(doctype)
	columns = get_readable_columns(meta)
	fields = fields or ["name"]
	_check_columns(fields, columns)
	options = dict(filters=_filters(meta, filters, columns), fields=fields, limit_page_length=_limit(limit))
	if order_by:
		options["order_by"] = _order_by(order_by, columns)
	return frappe.get_list(doctype, **options)


def get_doctype_fields(doctype: str) -> list[dict]:
	meta = get_readable_meta(doctype, allow_child=True)
	if not meta.istable:
		frappe.has_permission(doctype, "read", throw=True)
	return [
		{"fieldname": field.fieldname, "label": field.label, "fieldtype": field.fieldtype, "options": field.options}
		for field in meta.fields
		if (field.fieldtype in table_fields or field.fieldtype not in no_value_fields)
		and not is_sensitive_field(field.fieldname)
	]


def get_pages(doctype: str, name: str) -> dict:
	get_readable_meta(doctype)
	if not isinstance(name, str):
		raise ToolError("name must be text")
	pages, omitted = get_document_pages(doctype, name, limit=MAX_PAGES)
	return {"pages": pages, "omitted_pages": omitted}


def _check_fieldnames(meta, fields):
	if not isinstance(fields, list):
		raise ToolError("fields must be a list")
	for fieldname in fields:
		if not isinstance(fieldname, str) or not (fieldname in COLUMNS or meta.has_field(fieldname)):
			raise ToolError(f"Unknown field {fieldname} on {meta.name}")


def _check_columns(fields, columns):
	if not isinstance(fields, list):
		raise ToolError("fields must be a list")
	for fieldname in fields:
		if fieldname not in columns:
			raise ToolError(f"Field {fieldname} is unknown or not readable")


def _filters(meta, filters, columns):
	if isinstance(filters, dict):
		filters = [[key, *(value if _is_operator_pair(value) else ["=", value])] for key, value in filters.items()]
	if not isinstance(filters, list | type(None)):
		raise ToolError("filters must be a list or an object")
	return [_condition(meta, condition, columns) for condition in filters or []]


def _is_operator_pair(value):
	return isinstance(value, list) and len(value) == 2 and str(value[0]).lower() in OPERATORS


def _condition(meta, condition, columns):
	doctype = meta.name
	if isinstance(condition, list) and len(condition) == 4:
		doctype, condition = condition[0], condition[1:]
		columns = _filter_columns(meta, doctype, columns)
	if not isinstance(condition, list) or len(condition) != 3:
		raise ToolError("Each filter must be [field, operator, value] or [child DocType, field, operator, value]")
	fieldname, operator, value = condition
	if fieldname not in columns:
		raise ToolError(f"Filter field {fieldname} is unknown or not readable")
	if not isinstance(operator, str) or operator.lower() not in OPERATORS:
		raise ToolError(f"Operator {operator} is not allowed")
	values = value if isinstance(value, list) else [value]
	if not all(isinstance(item, SCALARS) for item in values):
		raise ToolError("Filter values must be scalars or lists of scalars")
	return [doctype, fieldname, operator, value]


def _filter_columns(meta, doctype, columns):
	"""Return the filterable columns of the DocType itself or of one of its child tables."""
	if doctype == meta.name:
		return columns
	if doctype not in {field.options for field in meta.get_table_fields()}:
		raise ToolError(f"{doctype} is not a child table of {meta.name}")
	child = frappe.get_meta(doctype)
	if is_denied_doctype(child):
		raise ToolError(f"DocType {doctype} is not available to AI tools")
	permitted = set(child.get_permitted_fieldnames(parenttype=meta.name, permission_type="read")) | COLUMNS
	return {fieldname for fieldname in permitted if not is_sensitive_field(fieldname)}


def _order_by(order_by, columns):
	match = ORDER_BY.fullmatch(order_by) if isinstance(order_by, str) else None
	if not match or match[1] not in columns:
		raise ToolError("order_by must be '<readable field> asc|desc'")
	return f"{match[1]} {(match[2] or 'asc').lower()}"


def _limit(limit):
	if not isinstance(limit, int) or isinstance(limit, bool):
		raise ToolError("limit must be an integer")
	return min(max(limit, 1), MAX_LIST_ROWS)


def _object(properties: dict, required: list[str]) -> dict:
	return {"type": "object", "properties": properties, "required": required}


TEXT = {"type": "string"}
FIELDS = {"type": "array", "items": TEXT}
READ_TOOLS = [
	Tool(
		"get_document",
		"Read one document with its child tables. _links maps each Link field to its DocType so you can follow it.",
		_object({"doctype": TEXT, "name": TEXT, "fields": FIELDS}, ["doctype", "name"]),
		get_document,
	),
	Tool(
		"get_list",
		"List documents of any readable DocType. filters: {field: value}, [[field, operator, value]] or "
		"[[child DocType, field, operator, value]] for a child-table field. Up to 50 rows.",
		_object(
			{
				"doctype": TEXT,
				"filters": {"type": ["array", "object"]},
				"fields": FIELDS,
				"order_by": TEXT,
				"limit": {"type": "integer"},
			},
			["doctype"],
		),
		get_list,
	),
	Tool(
		"get_doctype_fields",
		"List the fields of a DocType or child table; options give the Link target or child table DocType.",
		_object({"doctype": TEXT}, ["doctype"]),
		get_doctype_fields,
	),
	Tool(
		"get_document_pages",
		"Read the stored page data of a document's scans or attached files (at most 20 pages).",
		_object({"doctype": TEXT, "name": TEXT}, ["doctype", "name"]),
		get_pages,
	),
]

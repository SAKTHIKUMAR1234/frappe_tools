"""Shared value coercion: LLM string output -> Frappe field-typed values."""

from frappe.utils import flt, get_datetime, getdate


def coerce_value(fieldtype, value):
	if value is None:
		return None
	if isinstance(value, str):
		value = value.strip()
		if value == "":
			return None

	try:
		if fieldtype == "Check":
			return 1 if str(value).lower() in ("1", "true", "yes", "y", "on", "checked") else 0
		if fieldtype == "Int":
			return int(float(str(value).replace(",", "")))
		if fieldtype in ("Float", "Currency", "Percent"):
			return flt(str(value).replace(",", ""))
		if fieldtype == "Date":
			return str(getdate(value))
		if fieldtype == "Datetime":
			return str(get_datetime(value))
	except Exception:
		return value  # keep raw; let the form/insert surface a clear error

	return value

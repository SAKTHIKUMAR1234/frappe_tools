"""Safe declarative cross-field validation; configuration is data, never code."""

from frappe.utils import flt, getdate

from frappe_tools.extractors import schema as S


SUPPORTED = {"required_if", "equals", "date_order", "sum_equals"}


def validate(target_doctype, extraction):
	fields = {row.fieldname: row.value for row in extraction.extracted_fields}
	tables = {}
	for row in extraction.lines:
		import json
		tables.setdefault(row.table or extraction.line_table, []).append(json.loads(row.raw_json or "{}"))
	issues = []
	for rule in S.validation_rules(target_doctype):
		kind = rule.get("type")
		message = rule.get("message") or f"Validation failed: {kind}"
		try:
			if kind == "required_if" and fields.get(rule.get("when_field")) == rule.get("equals") and not fields.get(rule.get("field")):
				issues.append(message)
			elif kind == "equals" and fields.get(rule.get("field")) != fields.get(rule.get("other_field")):
				issues.append(message)
			elif kind == "date_order" and fields.get(rule.get("before")) and fields.get(rule.get("after")) and getdate(fields[rule["before"]]) > getdate(fields[rule["after"]]):
				issues.append(message)
			elif kind == "sum_equals":
				total = sum(flt(row.get(rule.get("column"))) for row in tables.get(rule.get("table"), []))
				if abs(total - flt(fields.get(rule.get("field")))) > flt(rule.get("tolerance") or 0.02):
					issues.append(message)
		except (TypeError, ValueError):
			issues.append(message)
	return issues

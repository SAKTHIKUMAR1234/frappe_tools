"""Compile adapter presentation into a complete, data-only client contract."""

import re


def manifest(plugin, ctx, schema=None):
	"""Every schema field/table remains reachable, including future adapter fields.

	UI sections may reorder facts, but cannot hide required review data or publish
	Python callables. Action execution still goes through the existing guarded API.
	"""
	schema = schema if schema is not None else plugin.schema(ctx)
	config = plugin.workflow(ctx) if callable(getattr(plugin, "workflow", None)) else {}
	config = config if isinstance(config, dict) else {}
	target = plugin.target_doctype
	fields = [field["fieldname"] for field in schema.get("header") or []]
	tables = {table["table"]: table for table in schema.get("tables") or []}
	seen_fields, seen_tables, seen_ids = set(), set(), set()
	sections = []
	for section in config.get("review_sections") or []:
		key = str(section.get("key") or "")
		if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) or key in seen_ids or key in {"checks", "matching"}:
			raise ValueError(f"Invalid or repeated workflow section: {key}")
		seen_ids.add(key)
		if section.get("table"):
			table = section["table"]
			if table not in tables or table in seen_tables:
				raise ValueError(f"Invalid or repeated workflow table: {table}")
			seen_tables.add(table)
			sections.append({"key": key, "label": section.get("label") or tables[table].get("label") or table,
				"kind": "table", "table": table})
		else:
			names = section.get("fields") or []
			if any(name not in fields or name in seen_fields for name in names) or len(names) != len(set(names)):
				raise ValueError(f"Invalid or repeated workflow field in {key}")
			seen_fields.update(names)
			if names or section.get("custom"):
				sections.append({"key": key, "label": section.get("label") or key,
					"kind": "fields" if names else "custom", "fields": names})
		if sections and sections[-1]["key"] == key:
			sections[-1]["title"] = section.get("title") or sections[-1]["label"]
			sections[-1]["description"] = section.get("description") or ""
	remaining = [name for name in fields if name not in seen_fields]
	if remaining:
		key = _unused_key("details", seen_ids)
		sections.append({"key": key, "label": "Other details" if sections else "Details", "kind": "fields", "fields": remaining})
	for table, spec in tables.items():
		if table not in seen_tables:
			key = _unused_key("table-" + table.replace("_", "-"), seen_ids)
			sections.append({"key": key, "label": spec.get("label") or table, "kind": "table", "table": table})
	icon = str(config.get("icon") or "pi pi-file")
	if not re.fullmatch(r"pi pi-[a-z-]+", icon):
		icon = "pi pi-file"
	return {
		"version": 1,
		"adapter_id": plugin.adapter_id(),
		"target_doctype": target,
		"label": config.get("label") or target,
		"description": config.get("description") or f"Extract and review {target}.",
		"icon": icon,
		"review_sections": sections,
		"action_label": config.get("action_label") or f"Create {target} draft",
		"action_description": config.get("action_description") or f"Save the reviewed {target} as a draft.",
		"result_description": config.get("result_description") or "Open the saved document to continue.",
		"matching_label": config.get("matching_label") or "Reference checks",
	}


def _unused_key(base, used):
	key, number = base, 1
	while key in used:
		number += 1
		key = f"{base}-{number}"
	used.add(key)
	return key

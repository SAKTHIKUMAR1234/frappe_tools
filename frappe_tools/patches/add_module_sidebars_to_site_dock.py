import importlib.util


def execute():
	"""Put the shipped module sidebars on a site dock that was saved before the app shipped a dock.

	A saved site dock hides entries the app adds later, and the v16 sidebar patch saved one that
	holds only the converted Scanner sidebar. Frappe 16 builds without the dock skip it.
	"""
	if not importlib.util.find_spec("frappe.desk.doctype.dock"):
		return

	from frappe.desk.doctype.dock.dock import get_app_dock, get_dock, resolve_app_dock, save_site_dock

	if not get_dock("frappe_tools"):
		return

	rail = resolve_app_dock("frappe_tools", upto="site", gated=False)
	on_rail = {(entry["link_type"], entry["link_to"]) for entry in rail}
	missing = [entry for entry in get_app_dock("frappe_tools") if (entry["link_type"], entry["link_to"]) not in on_rail]
	if not missing:
		return

	save_site_dock("frappe_tools", [*missing, *rail])

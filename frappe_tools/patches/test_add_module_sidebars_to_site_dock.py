from frappe.desk.doctype.dock.dock import resolve_app_dock, save_site_dock
from frappe.tests.utils import FrappeTestCase

from frappe_tools.patches import add_module_sidebars_to_site_dock


def site_rail() -> list[str]:
	return [entry["link_to"] for entry in resolve_app_dock("frappe_tools", upto="site", gated=False)]


class TestAddModuleSidebarsToSiteDock(FrappeTestCase):
	def test_adds_shipped_sidebars_missing_from_site_dock(self):
		save_site_dock("frappe_tools", [{"link_type": "Sidebar", "link_to": "Frappe Tools"}])

		add_module_sidebars_to_site_dock.execute()
		add_module_sidebars_to_site_dock.execute()

		self.assertEqual(site_rail(), ["Scanning", "Frappe Tools"])

import frappe
from frappe.client import get
from frappe.tests.utils import FrappeTestCase

from frappe_tools.patches import set_catch_all_layouts_auto_capture

LAYOUT = "Document Scanner Layout"


def make_layout(name: str, is_catch_all: int) -> str:
	return frappe.get_doc({
		"doctype": LAYOUT, "layout_name": name, "layout_doctype": "ToDo", "is_catch_all": is_catch_all,
	}).insert().name


class TestSetCatchAllLayoutsAutoCapture(FrappeTestCase):
	def test_turns_on_auto_capture_only_for_catch_all_layouts(self):
		catch_all = make_layout("_Test Auto Capture All", 1)
		specific = make_layout("_Test Auto Capture Bills", 0)

		set_catch_all_layouts_auto_capture.execute()
		set_catch_all_layouts_auto_capture.execute()

		self.assertEqual(frappe.db.get_value(LAYOUT, catch_all, "auto_capture"), 1)
		self.assertEqual(frappe.db.get_value(LAYOUT, specific, "auto_capture"), 0)

	def test_layout_fetch_carries_auto_capture_flag(self):
		name = make_layout("_Test Auto Capture Fetch", 0)
		frappe.db.set_value(LAYOUT, name, "auto_capture", 1)

		self.assertEqual(get(LAYOUT, name)["auto_capture"], 1)

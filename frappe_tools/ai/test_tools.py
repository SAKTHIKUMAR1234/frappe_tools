from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from frappe_tools.ai import tools
from frappe_tools.ai.tools import ToolError

DENIED = [
	"AI Provider Account",
	"Document Extraction Settings",
	"User",
	"Integration Request",
	"Webhook",
	"Connected App",
	"Token Cache",
	"Social Login Key",
	"Email Account",
	"System Settings",
	"Error Log",
	"Access Log",
	"Version",
	"Document AI Call Log",
	"OAuth Client",
	"OAuth Bearer Token",
	"Bank Account",
]


class Meta(frappe._dict):
	def get_field(self, fieldname):
		return next((field for field in self.fields if field.fieldname == fieldname), None)


def field(fieldname, fieldtype="Data", options=None):
	return frappe._dict(fieldname=fieldname, fieldtype=fieldtype, options=options)


PARENT = Meta(name="Local Parent", fields=[
	field("supplier", "Link", "Supplier"), field("party_type"), field("party", "Dynamic Link", "party_type"),
	field("login", "Password"), field("items", "Table", "Local Child"), field("api_key"), field("total", "Float"),
])
CHILD = Meta(name="Local Child", fields=[field("item_code", "Link", "Item"), field("pin", "Password"), field("qty", "Float")])


class TestTools(UnitTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.todo = frappe.get_doc(dict(doctype="ToDo", description="local tool test", allocated_to="Administrator")).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_denied_doctypes_for_every_tool(self):
		calls = [
			lambda doctype: tools.get_document(doctype, "x"),
			lambda doctype: tools.get_list(doctype),
			lambda doctype: tools.get_pages(doctype, "x"),
		]
		for doctype in DENIED:
			if not frappe.db.exists("DocType", doctype):
				continue
			for call in calls:
				with self.subTest(doctype=doctype), self.assertRaisesRegex(ToolError, "not available"):
					call(doctype)

	def test_password_field_denies_any_doctype(self):
		meta = Meta(name="Local Settings", issingle=0, is_virtual=0, fields=[field("pin", "Password")])
		self.assertTrue(tools.is_denied_doctype(meta))
		meta.fields = [field("pin")]
		self.assertFalse(tools.is_denied_doctype(meta))

	def test_child_tables_only_for_field_lists(self):
		with self.assertRaisesRegex(ToolError, "not available"):
			tools.get_list("Has Role")
		self.assertIn("role", [row["fieldname"] for row in tools.get_doctype_fields("Has Role")])

	def test_unknown_doctype(self):
		for doctype in ("Local Missing DocType", {"name": "ToDo"}):
			with self.assertRaisesRegex(ToolError, "Unknown DocType"):
				tools.get_list(doctype)

	def test_password_and_sensitive_fields_stripped_in_child_rows(self):
		record = {"supplier": "S-1", "login": "hash", "api_key": "k", "__onload": {}, "total": 5,
			"items": [{"item_code": "I-1", "pin": "1234", "qty": 2}]}
		with patch.object(tools.frappe, "get_meta", return_value=CHILD):
			self.assertEqual(tools.clean_record(record, PARENT),
				{"supplier": "S-1", "total": 5, "items": [{"item_code": "I-1", "qty": 2}]})

	def test_link_targets_cover_dynamic_links_and_child_rows(self):
		data = {"supplier": "S-1", "party_type": "Customer", "party": "C-1", "items": [{"item_code": "I-1"}]}
		with patch.object(tools.frappe, "get_meta", return_value=CHILD):
			self.assertEqual(tools.get_link_targets(PARENT, data),
				{"supplier": "Supplier", "party": "Customer", "items.item_code": "Item"})

	def test_get_document_reads_with_permission_and_selected_fields(self):
		data = tools.get_document("ToDo", self.todo.name)
		self.assertEqual(data["description"], "local tool test")
		self.assertEqual(data["_links"]["allocated_to"], "User")
		data = tools.get_document("ToDo", self.todo.name, ["description"])
		self.assertEqual(set(data), {"name", "description", "_links"})
		with self.assertRaisesRegex(ToolError, "Unknown field"):
			tools.get_document("ToDo", self.todo.name, ["no_such_field"])
		with self.assertRaisesRegex(ToolError, "name must be text"):
			tools.get_document("ToDo", {"description": "local tool test"})

	def test_session_user_permissions_apply(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			tools.get_document("ToDo", self.todo.name)
		with self.assertRaises(frappe.PermissionError):
			tools.get_list("ToDo")

	def test_get_list_filters_fields_order_and_limit(self):
		for filters in ({"description": "local tool test"}, {"description": ["like", "local tool%"]},
			[["description", "=", "local tool test"]], [["ToDo", "description", "=", "local tool test"]]):
			with self.subTest(filters=filters):
				rows = tools.get_list("ToDo", filters, ["name", "description"], "creation desc", 500)
				self.assertEqual([row.name for row in rows], [self.todo.name])
		with patch.object(tools.frappe, "get_list", return_value=[]) as get_list:
			tools.get_list("ToDo", limit=500)
		self.assertEqual(get_list.call_args.kwargs["limit_page_length"], tools.MAX_LIST_ROWS)

	def test_get_list_rejects_bad_arguments(self):
		cases = [
			dict(fields=["description", "count(name)"]),
			dict(fields="name"),
			dict(filters=[["no_such_field", "=", 1]]),
			dict(filters=[["description", "; drop", 1]]),
			dict(filters=[["description", "=", {"nested": 1}]]),
			dict(filters="description = 1"),
			dict(filters=[["Local Other", "description", "=", 1]]),
			dict(order_by="creation; drop table"),
			dict(order_by="no_such_field desc"),
			dict(limit="5"),
		]
		for kwargs in cases:
			with self.subTest(kwargs=kwargs), self.assertRaises(ToolError):
				tools.get_list("ToDo", **kwargs)

	def test_sensitive_names_never_selectable_or_filterable(self):
		meta = Meta(name="Local Parent", get_permitted_fieldnames=lambda **kwargs: ["total", "api_key", "access_token"])
		columns = tools.get_readable_columns(meta)
		self.assertIn("total", columns)
		self.assertFalse({"api_key", "access_token"} & columns)

	def test_child_table_filters(self):
		profile = frappe.get_doc(dict(doctype="Role Profile", role_profile="Local Tool " + frappe.generate_hash(length=6),
			roles=[{"role": "System Manager"}])).insert()
		rows = tools.get_list("Role Profile", [["Has Role", "role", "=", "System Manager"]], limit=50)
		self.assertIn(profile.name, [row.name for row in rows])
		with self.assertRaisesRegex(ToolError, "not a child table"):
			tools.get_list("Role Profile", [["ToDo", "description", "=", "x"]])
		with self.assertRaisesRegex(ToolError, "unknown or not readable"):
			tools.get_list("Role Profile", [["Has Role", "no_such_field", "=", "x"]])

	def test_doctype_fields_show_link_and_table_options(self):
		fields = {row["fieldname"]: row for row in tools.get_doctype_fields("Role Profile")}
		self.assertEqual(fields["roles"]["options"], "Has Role")
		self.assertNotIn("Section Break", {row["fieldtype"] for row in fields.values()})
		self.assertEqual({row["fieldname"]: row for row in tools.get_doctype_fields("ToDo")}["allocated_to"]["options"], "User")

	def test_pages_use_file_data_with_page_limit(self):
		with patch.object(tools, "get_document_pages", return_value=([{"page_no": 1}], 3)) as pages:
			self.assertEqual(tools.get_pages("ToDo", self.todo.name), {"pages": [{"page_no": 1}], "omitted_pages": 3})
		pages.assert_called_once_with("ToDo", self.todo.name, limit=tools.MAX_PAGES)

	def test_read_tools_are_the_only_tools(self):
		self.assertEqual([tool.name for tool in tools.READ_TOOLS],
			["get_document", "get_list", "get_doctype_fields", "get_document_pages"])

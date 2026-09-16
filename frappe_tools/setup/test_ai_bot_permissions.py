import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime

from frappe_tools import hooks
from frappe_tools.setup.ai_bot_permissions import (
	ROLE_NAME,
	_child_doctypes,
	_clear_permission_caches,
	_insert_ai_bot_row,
	_standalone_doctypes,
	cleanup_ai_bot_rows_on_child_doctypes,
	ensure_role,
	setup_doctype_permissions,
)


class TestAIBotPermissionCacheSafety(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.sid = f"frappe-tools-cache-test-{frappe.generate_hash(length=10)}"
		self.user = f"frappe-tools-cache-test-{frappe.generate_hash(length=10)}"

	def tearDown(self):
		frappe.cache.hdel("session", self.sid)
		frappe.cache.hdel("roles", self.user)
		super().tearDown()

	def test_permission_cache_clear_does_not_clear_sessions(self):
		frappe.cache.hset("session", self.sid, {"user": "cache-test@example.com"})
		frappe.cache.hset("roles", self.user, ["Cache Test Role"])

		_clear_permission_caches()

		self.assertEqual(
			frappe.cache.hget("session", self.sid),
			{"user": "cache-test@example.com"},
		)
		self.assertIsNone(frappe.cache.hget("roles", self.user))

	def test_roles_are_not_exported_as_destructive_fixtures(self):
		self.assertFalse(hasattr(hooks, "fixtures"))
		self.assertFalse(os.path.exists(frappe.get_app_path("frappe_tools", "fixtures", "role.json")))

	def test_existing_role_is_updated_without_deleting_it(self):
		role_name = f"In-place Role {frappe.generate_hash(length=10)}"
		role = frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": role_name,
				"desk_access": 1,
			}
		).insert(ignore_permissions=True)
		creation = role.creation

		try:
			with patch("frappe.delete_doc") as delete_doc:
				ensure_role(role_name, desk_access=0)

			delete_doc.assert_not_called()
			updated = frappe.get_doc("Role", role_name)
			self.assertEqual(get_datetime(updated.creation), get_datetime(creation))
			self.assertEqual(updated.desk_access, 0)
		finally:
			frappe.db.delete("Role", {"name": role_name})


class TestAiBotSkipsChildDoctypes(FrappeTestCase):
	"""Direct AI Bot DocPerm on istable DocTypes is invalid; child access is
	inherited from the parent (Error Log → Logs To Clear is the known break)."""

	def test_seed_list_excludes_every_child_doctype(self):
		child = set(_child_doctypes())
		standalone = set(_standalone_doctypes())
		self.assertTrue(child)
		self.assertTrue(standalone)
		self.assertFalse(child & standalone)
		self.assertIn("Logs To Clear", child)
		self.assertIn("Error Log", standalone)
		self.assertIn("Log Settings", standalone)

	def test_setup_does_not_insert_ai_bot_rows_on_child_doctypes(self):
		child = "Logs To Clear"
		self.assertEqual(cint_istable(child), 1)

		frappe.db.delete("DocPerm", {"parent": child, "role": ROLE_NAME})
		frappe.db.delete("Custom DocPerm", {"parent": child, "role": ROLE_NAME})

		with patch.object(frappe.db, "commit"):
			setup_doctype_permissions(protected_doctypes=set())

		self.assertFalse(frappe.db.exists("DocPerm", {"parent": child, "role": ROLE_NAME}))
		self.assertFalse(frappe.db.exists("Custom DocPerm", {"parent": child, "role": ROLE_NAME}))

	def test_cleanup_strips_existing_ai_bot_rows_on_child_doctypes(self):
		child = "Logs To Clear"
		self.assertEqual(cint_istable(child), 1)

		if not frappe.db.exists("DocPerm", {"parent": child, "role": ROLE_NAME}):
			_insert_ai_bot_row("DocPerm", child, 0)
		self.assertTrue(frappe.db.exists("DocPerm", {"parent": child, "role": ROLE_NAME}))

		with patch.object(frappe.db, "commit"):
			cleanup_ai_bot_rows_on_child_doctypes()

		self.assertFalse(frappe.db.exists("DocPerm", {"parent": child, "role": ROLE_NAME}))
		self.assertFalse(frappe.db.exists("Custom DocPerm", {"parent": child, "role": ROLE_NAME}))

	def test_parent_log_settings_still_gets_ai_bot_read(self):
		parent = "Log Settings"
		self.assertEqual(cint_istable(parent), 0)
		with patch.object(frappe.db, "commit"):
			setup_doctype_permissions(protected_doctypes=set())
		has_std = frappe.db.exists("DocPerm", {"parent": parent, "role": ROLE_NAME, "read": 1})
		has_custom = frappe.db.exists("Custom DocPerm", {"parent": parent, "role": ROLE_NAME, "read": 1})
		self.assertTrue(has_std or has_custom)


def cint_istable(doctype):
	return int(frappe.db.get_value("DocType", doctype, "istable") or 0)

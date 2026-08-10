import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime

from frappe_tools import hooks
from frappe_tools.setup.ai_bot_permissions import _clear_permission_caches, ensure_role


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

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from frappe_tools import hooks
from frappe_tools.setup.ai_bot_permissions import _clear_permission_caches


class TestAIBotPermissionCacheSafety(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.sid = f"frappe-tools-cache-test-{frappe.generate_hash(length=10)}"
		self.user = f"frappe-tools-cache-test-{frappe.generate_hash(length=10)}"

	def tearDown(self):
		frappe.cache.hdel("session", self.sid)
		frappe.cache.hdel("roles", self.user)
		super().tearDown()

	def test_global_cache_clear_preserves_session_hash(self):
		frappe.cache.hset("session", self.sid, {"user": "cache-test@example.com"})

		frappe.clear_cache()

		self.assertEqual(
			frappe.cache.hget("session", self.sid),
			{"user": "cache-test@example.com"},
		)

	def test_permission_cache_clear_does_not_clear_sessions(self):
		frappe.cache.hset("session", self.sid, {"user": "cache-test@example.com"})
		frappe.cache.hset("roles", self.user, ["Cache Test Role"])

		_clear_permission_caches()

		self.assertEqual(
			frappe.cache.hget("session", self.sid),
			{"user": "cache-test@example.com"},
		)
		self.assertIsNone(frappe.cache.hget("roles", self.user))

	def test_ai_bot_role_is_not_reimported_during_migration(self):
		"""Role fixture re-imports force-logout users assigned non-Desk roles."""
		with open(frappe.get_app_path("frappe_tools", "fixtures", "role.json")) as fixture_file:
			fixture_roles = json.load(fixture_file)

		exported_roles = hooks.fixtures[0]["filters"][0][2]
		self.assertNotIn("AI Bot", exported_roles)
		self.assertNotIn("AI Bot", {role["name"] for role in fixture_roles})

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

	def test_permission_cache_clear_does_not_clear_sessions(self):
		frappe.cache.hset("session", self.sid, {"user": "cache-test@example.com"})
		frappe.cache.hset("roles", self.user, ["Cache Test Role"])

		_clear_permission_caches()

		self.assertEqual(
			frappe.cache.hget("session", self.sid),
			{"user": "cache-test@example.com"},
		)
		self.assertIsNone(frappe.cache.hget("roles", self.user))

	def test_ai_bot_role_is_created_idempotently_instead_of_exported(self):
		exported_roles = hooks.fixtures[0]["filters"][0][2]
		self.assertNotIn("AI Bot", exported_roles)

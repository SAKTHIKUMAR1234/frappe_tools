import frappe
from frappe.tests.utils import FrappeTestCase

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

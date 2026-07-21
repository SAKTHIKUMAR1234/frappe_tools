# Copyright (c) 2026, sakthi123msd@gmail.com and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from frappe_tools import permissions


class _Doc:
	def __init__(self, doctype, ignore=False):
		self.doctype = doctype
		self.flags = type("F", (), {"ignore_permissions": ignore})()


class TestAiBotPermission(FrappeTestCase):
	"""AI Bot: read everything, write NOTHING except the configured allowlist.
	Enforced by (a) the version-aware has_permission hook and (b) a hard
	document-lifecycle guard that catches paths the hook can't."""

	def setUp(self):
		# fixed allowlist for deterministic tests
		self._p = patch.object(permissions, "_write_allowed_doctypes", return_value={"AI Bot Page"})
		self._p.start()

	def tearDown(self):
		self._p.stop()

	# ---- has_permission hook: reads ----
	def test_v15_neutral_for_non_aibot(self):
		with patch.object(permissions, "_is_v16", return_value=False), \
		     patch.object(permissions.frappe, "get_roles", return_value=[]):
			self.assertIsNone(permissions.ai_bot_has_permission(_Doc("Sales Invoice"), "read", "u@x.com"))
			self.assertIsNone(permissions.ai_bot_has_permission(_Doc("Sales Invoice"), "write", "u@x.com"))

	def test_v15_true_for_aibot_reads(self):
		with patch.object(permissions, "_is_v16", return_value=False), \
		     patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			self.assertTrue(permissions.ai_bot_has_permission(_Doc("Sales Invoice"), "read", "bot@x.com"))
			self.assertTrue(permissions.ai_bot_has_permission(_Doc("Sales Invoice"), "print", "bot@x.com"))

	# ---- has_permission hook: write deny + allowlist ----
	def test_v15_aibot_write_denied_off_allowlist(self):
		with patch.object(permissions, "_is_v16", return_value=False), \
		     patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			self.assertFalse(permissions.ai_bot_has_permission(_Doc("Sales Invoice"), "write", "bot@x.com"))
			self.assertFalse(permissions.ai_bot_has_permission(_Doc("User"), "create", "bot@x.com"))

	def test_v15_aibot_write_allowed_on_allowlist(self):
		with patch.object(permissions, "_is_v16", return_value=False), \
		     patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			# allowlisted → neutral (None) so the DocPerm row grants it
			self.assertIsNone(permissions.ai_bot_has_permission(_Doc("AI Bot Page"), "write", "bot@x.com"))

	def test_system_manager_never_restricted(self):
		with patch.object(permissions, "_is_v16", return_value=False), \
		     patch.object(permissions.frappe, "get_roles", return_value=["AI Bot", "System Manager"]):
			# has both roles → not treated as AI Bot; write not denied
			self.assertIsNone(permissions.ai_bot_has_permission(_Doc("Sales Invoice"), "write", "sm@x.com"))

	def test_v16_never_returns_falsy_for_allowed(self):
		with patch.object(permissions, "_is_v16", return_value=True), \
		     patch.object(permissions.frappe, "get_roles", return_value=[]):
			for ptype in ("read", "write", "create", "delete"):
				self.assertTrue(permissions.ai_bot_has_permission(_Doc("Sales Invoice"), ptype, "u@x.com"))

	def test_v16_aibot_write_denied_off_allowlist(self):
		with patch.object(permissions, "_is_v16", return_value=True), \
		     patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			self.assertFalse(permissions.ai_bot_has_permission(_Doc("Sales Invoice"), "write", "bot@x.com"))
			self.assertTrue(permissions.ai_bot_has_permission(_Doc("AI Bot Page"), "write", "bot@x.com"))

	# ---- the hard write guard ----
	def test_guard_blocks_aibot_off_allowlist(self):
		with patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			with self.assertRaises(frappe.PermissionError):
				permissions.ai_bot_guard_write(_Doc("User"))
			with self.assertRaises(frappe.PermissionError):
				permissions.ai_bot_guard_write(_Doc("Sales Invoice"))

	def test_guard_allows_aibot_on_allowlist(self):
		with patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			permissions.ai_bot_guard_write(_Doc("AI Bot Page"))  # no raise

	def test_guard_ignores_non_aibot_and_ignore_perms(self):
		with patch.object(permissions.frappe, "get_roles", return_value=["Sales User"]):
			permissions.ai_bot_guard_write(_Doc("Sales Invoice"))  # non-AI-Bot → no raise
		with patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			permissions.ai_bot_guard_write(_Doc("Sales Invoice", ignore=True))  # trusted → no raise
		with patch.object(permissions.frappe, "get_roles", return_value=["AI Bot", "System Manager"]):
			permissions.ai_bot_guard_write(_Doc("Sales Invoice"))  # SM → no raise

	def test_query_conditions_unchanged(self):
		with patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			self.assertEqual(permissions.ai_bot_query_conditions(user="bot@x.com"), "")
		with patch.object(permissions.frappe, "get_roles", return_value=[]):
			self.assertIsNone(permissions.ai_bot_query_conditions(user="u@x.com"))

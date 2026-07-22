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
	"""AI Bot: read EVERYTHING, write NOTHING. The role grants no write rows, so
	an AI-Bot-only user can't write any doctype. On top of that, a hard guard
	blocks writes to the privilege-escalation surface (User/Role/DocPerm/...)
	even via Frappe built-in bypasses. Any real write functionality lives on a
	SEPARATE role — never AI Bot — so writes on ordinary doctypes are left to
	DocPerm (neutral here), not blocked."""

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

	# ---- has_permission hook: escalation writes denied, ordinary writes neutral ----
	def test_v15_aibot_escalation_write_denied(self):
		with patch.object(permissions, "_is_v16", return_value=False), \
		     patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			self.assertFalse(permissions.ai_bot_has_permission(_Doc("User"), "write", "bot@x.com"))
			self.assertFalse(permissions.ai_bot_has_permission(_Doc("Role"), "create", "bot@x.com"))
			self.assertFalse(permissions.ai_bot_has_permission(_Doc("Custom DocPerm"), "delete", "bot@x.com"))

	def test_v15_aibot_ordinary_write_neutral(self):
		with patch.object(permissions, "_is_v16", return_value=False), \
		     patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			# not escalation → neutral (None): DocPerm/other roles decide. AI Bot
			# has no write row, so an AI-Bot-only user is still denied by DocPerm.
			self.assertIsNone(permissions.ai_bot_has_permission(_Doc("Sales Invoice"), "write", "bot@x.com"))
			self.assertIsNone(permissions.ai_bot_has_permission(_Doc("Custom User Dashboard"), "write", "bot@x.com"))

	def test_system_manager_never_restricted(self):
		with patch.object(permissions, "_is_v16", return_value=False), \
		     patch.object(permissions.frappe, "get_roles", return_value=["AI Bot", "System Manager"]):
			# has both roles → not treated as AI Bot; escalation write not denied
			self.assertIsNone(permissions.ai_bot_has_permission(_Doc("User"), "write", "sm@x.com"))

	def test_v16_never_returns_falsy_for_allowed(self):
		with patch.object(permissions, "_is_v16", return_value=True), \
		     patch.object(permissions.frappe, "get_roles", return_value=[]):
			for ptype in ("read", "write", "create", "delete"):
				self.assertTrue(permissions.ai_bot_has_permission(_Doc("Sales Invoice"), ptype, "u@x.com"))

	def test_v16_aibot_escalation_write_denied(self):
		with patch.object(permissions, "_is_v16", return_value=True), \
		     patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			self.assertFalse(permissions.ai_bot_has_permission(_Doc("User"), "write", "bot@x.com"))
			# ordinary doctype → not denied (True), DocPerm still governs the write
			self.assertTrue(permissions.ai_bot_has_permission(_Doc("Sales Invoice"), "write", "bot@x.com"))

	# ---- the hard write guard ----
	def test_guard_blocks_aibot_on_escalation(self):
		with patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			with self.assertRaises(frappe.PermissionError):
				permissions.ai_bot_guard_write(_Doc("User"))
			with self.assertRaises(frappe.PermissionError):
				permissions.ai_bot_guard_write(_Doc("Has Role"))

	def test_guard_allows_aibot_on_ordinary_doctype(self):
		# ordinary doctypes aren't guarded here — an AI-Bot-only user is already
		# blocked by DocPerm; a user with a separate write-role must not be.
		with patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			permissions.ai_bot_guard_write(_Doc("Custom User Dashboard"))  # no raise
			permissions.ai_bot_guard_write(_Doc("Sales Invoice"))  # no raise

	def test_guard_ignores_non_aibot_and_ignore_perms(self):
		with patch.object(permissions.frappe, "get_roles", return_value=["Sales User"]):
			permissions.ai_bot_guard_write(_Doc("User"))  # non-AI-Bot → no raise
		with patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			permissions.ai_bot_guard_write(_Doc("User", ignore=True))  # trusted → no raise
		with patch.object(permissions.frappe, "get_roles", return_value=["AI Bot", "System Manager"]):
			permissions.ai_bot_guard_write(_Doc("User"))  # SM → no raise

	def test_query_conditions_unchanged(self):
		with patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			self.assertEqual(permissions.ai_bot_query_conditions(user="bot@x.com"), "")
		with patch.object(permissions.frappe, "get_roles", return_value=[]):
			self.assertIsNone(permissions.ai_bot_query_conditions(user="u@x.com"))

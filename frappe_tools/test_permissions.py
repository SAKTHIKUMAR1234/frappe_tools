# Copyright (c) 2026, sakthi123msd@gmail.com and Contributors
# See license.txt

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from frappe_tools import permissions


class TestAiBotPermission(FrappeTestCase):
	"""The wildcard '*' has_permission hook must be correct on BOTH Frappe versions:
	- v15: the '*' hook runs first and a non-None return short-circuits the whole
	  controller check, so it must stay NEUTRAL (None) for everyone except a
	  belt-and-braces True for AI Bot reads. Blanket-True would bypass every other
	  app's deny hook (security hole).
	- v16: a controller hook denies on ANY falsy return, so it must never return
	  falsy → always True (a non-denying no-op)."""

	def test_v15_neutral_for_non_aibot(self):
		with patch.object(permissions, "_is_v16", return_value=False), \
		     patch.object(permissions.frappe, "get_roles", return_value=[]):
			# None = neutral; lets other apps' controller deny hooks run.
			self.assertIsNone(permissions.ai_bot_has_permission(None, "read", "u@x.com"))
			self.assertIsNone(permissions.ai_bot_has_permission(None, "write", "u@x.com"))
			self.assertIsNone(permissions.ai_bot_has_permission(None, "delete", "u@x.com"))

	def test_v15_true_only_for_aibot_reads(self):
		with patch.object(permissions, "_is_v16", return_value=False), \
		     patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			self.assertTrue(permissions.ai_bot_has_permission(None, "read", "bot@x.com"))
			self.assertTrue(permissions.ai_bot_has_permission(None, "print", "bot@x.com"))
			# non-allowed ptype stays neutral even for AI Bot
			self.assertIsNone(permissions.ai_bot_has_permission(None, "write", "bot@x.com"))

	def test_v16_never_returns_falsy(self):
		# v16 denies on any falsy controller return -> must be truthy for all users/ptypes.
		with patch.object(permissions, "_is_v16", return_value=True):
			for ptype in ("read", "write", "create", "delete", "submit", "cancel"):
				self.assertTrue(
					permissions.ai_bot_has_permission(None, ptype, "anyone@x.com"),
					f"v16: returned falsy for ptype={ptype} -> would DENY",
				)

	def test_query_conditions_unchanged(self):
		with patch.object(permissions.frappe, "get_roles", return_value=["AI Bot"]):
			self.assertEqual(permissions.ai_bot_query_conditions(user="bot@x.com"), "")
		with patch.object(permissions.frappe, "get_roles", return_value=[]):
			self.assertIsNone(permissions.ai_bot_query_conditions(user="u@x.com"))

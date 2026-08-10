from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from frappe_tools.overrides.role import Role


class TestRoleFixtureImportSafety(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.role_name = f"Fixture Safety {frappe.generate_hash(length=10)}"
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": self.role_name,
				"desk_access": 1,
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.db.delete("Role", {"name": self.role_name})
		frappe.clear_cache(doctype="Role")
		super().tearDown()

	def imported_role(self, desk_access):
		role = Role(
			{
				"doctype": "Role",
				"name": self.role_name,
				"role_name": self.role_name,
				"desk_access": desk_access,
			}
		)
		role.before_import()
		return role

	def test_unchanged_fixture_does_not_recalculate_users(self):
		role = self.imported_role(desk_access=1)

		with patch.object(Role, "update_user_type_on_change") as recalculate:
			role.on_update()

		recalculate.assert_not_called()

	def test_real_fixture_change_recalculates_users(self):
		role = self.imported_role(desk_access=0)

		with patch.object(Role, "update_user_type_on_change") as recalculate:
			role.on_update()

		recalculate.assert_called_once()

	def test_new_role_keeps_standard_recalculation(self):
		role = Role(
			{
				"doctype": "Role",
				"name": f"New {self.role_name}",
				"role_name": f"New {self.role_name}",
				"desk_access": 1,
			}
		)
		role.before_import()

		with patch.object(Role, "update_user_type_on_change") as recalculate:
			role.on_update()

		recalculate.assert_called_once()

import frappe
from frappe.core.doctype.role.role import Role as FrappeRole
from frappe.utils import cint


class Role(FrappeRole):
	"""Avoid false desk-access changes when an existing Role is imported.

	Frappe's fixture importer deletes an existing document before inserting its
	replacement. The newly inserted Role has no ``doc_before_save``, so the core
	controller treats ``desk_access`` as changed even when the fixture contains
	the same value. That unnecessary user-type recalculation can force-log out
	every user assigned to the Role.

	Capture the persisted value before the importer deletes it, then re-evaluate
	users only when the fixture really changes ``desk_access``. New Roles and
	normal Role saves retain the standard Frappe behaviour.
	"""

	def before_import(self):
		persisted_desk_access = frappe.db.get_value("Role", self.name, "desk_access")
		self.flags.role_existed_before_import = persisted_desk_access is not None
		self.flags.desk_access_before_import = persisted_desk_access

	def on_update(self):
		if frappe.flags.in_install:
			return

		if self.flags.get("role_existed_before_import"):
			if cint(self.flags.desk_access_before_import) != cint(self.desk_access):
				self.update_user_type_on_change()
			return

		super().on_update()

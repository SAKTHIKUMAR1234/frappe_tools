import frappe

__version__ = "0.0.1"

APP_ROLES = {"System Manager", "Scanner User", "Accounts Manager"}


def check_app_permission() -> bool:
	"""Show Frappe Tools on the apps screen to its managers, scanner users and verifier users."""
	if frappe.session.user == "Administrator":
		return True

	return bool(APP_ROLES.intersection(frappe.get_roles()))

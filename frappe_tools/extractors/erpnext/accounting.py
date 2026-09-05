import frappe


def account_is_valid(account, company, root_types=None):
	if not account or not frappe.db.exists("Account", account):
		return False
	row = frappe.db.get_value("Account", account, ["company", "is_group", "disabled", "root_type"], as_dict=True)
	return bool(row and row.company == company and not row.is_group and not row.disabled and
		(not root_types or row.root_type in set(root_types)))

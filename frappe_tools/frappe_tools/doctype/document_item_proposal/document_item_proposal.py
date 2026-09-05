import re

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_tools.extractors.erpnext.accounting import account_is_valid


class DocumentItemProposal(Document):
	def validate(self):
		if self.status not in {"Draft", "Approved", "Created", "Rejected"}:
			frappe.throw(_("Invalid proposal status."))
		for fieldname, roots in (("expense_account", {"Expense", "Asset"}), ("income_account", {"Income"})):
			account = self.get(fieldname)
			if account and not account_is_valid(account, self.company, roots):
				frappe.throw(_("{0} is not a valid posting account for {1}.").format(account, self.company))


@frappe.whitelist()
def approve_and_create(name):
	"""Explicit human action; the agent runtime cannot call this mutation."""
	doc = frappe.get_doc("Document Item Proposal", name)
	doc.check_permission("write")
	locked = frappe.db.sql("select status, created_item from `tabDocument Item Proposal` where name=%s for update", name, as_dict=True)[0]
	if locked.created_item and frappe.db.exists("Item", locked.created_item):
		return {"item": locked.created_item, "already_created": True}
	if locked.status not in {"Draft", "Approved"}:
		frappe.throw(_("Only a draft proposal can create an Item."))
	if frappe.db.exists("Item", doc.item_code):
		frappe.throw(_("Item {0} already exists; map the row to it instead.").format(doc.item_code))
	item = frappe.new_doc("Item")
	item.update({"item_code": doc.item_code, "item_name": doc.item_name, "item_group": doc.item_group,
		"stock_uom": doc.stock_uom, "is_purchase_item": 1, "description": doc.printed_description})
	digits = re.sub(r"\D", "", doc.printed_hsn or "")
	if len(digits) in {6, 8} and item.meta.has_field("gst_hsn_code"):
		item.gst_hsn_code = digits
	if item.meta.has_field("item_defaults") and (doc.expense_account or doc.income_account):
		item.append("item_defaults", {"company": doc.company, "expense_account": doc.expense_account, "income_account": doc.income_account})
	if doc.item_tax_template and item.meta.has_field("taxes"):
		item.append("taxes", {"item_tax_template": doc.item_tax_template})
	item.insert()
	doc.db_set({"status": "Created", "created_item": item.name})
	extraction = frappe.get_doc("Document Extraction", doc.extraction)
	line = next((row for row in extraction.lines if int(row.row_no) == int(doc.row_no)), None)
	if line:
		line.matched_item = item.name
		line.resolution_status = "New Item"
		line.match_method = "human-approved-item-proposal"
		line.match_confidence = 1
		extraction.save(ignore_permissions=True)
	return {"item": item.name, "already_created": False}

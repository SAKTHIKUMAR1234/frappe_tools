import frappe
from frappe.model.document import Document

VERIFIER = "Scanned Document AI Verifier"
READ_TYPES = (None, "read", "select", "print", "email", "export", "report")


class DocumentAICallLog(Document):
	pass


def has_permission(doc: Document, user: str | None = None, ptype: str | None = None) -> bool:
	user = user or frappe.session.user
	if "System Manager" in frappe.get_roles(user):
		return True
	if ptype not in READ_TYPES or not doc.reference_name:
		return False
	if doc.reference_doctype == VERIFIER:
		return frappe.db.get_value(VERIFIER, doc.reference_name, "owner") == user
	if doc.reference_doctype == "Scanned Document":
		return _can_read_scanned_document(doc.reference_name, user)
	return False


def _can_read_scanned_document(name: str, user: str) -> bool:
	scan = frappe.db.get_value("Scanned Document", name, ["_doctype", "_docname"], as_dict=True)
	return bool(scan and scan._doctype and scan._docname and frappe.db.exists(scan._doctype, scan._docname)
		and frappe.has_permission(scan._doctype, "read", doc=scan._docname, user=user))


def permission_query_conditions(user: str | None = None) -> str:
	user = user or frappe.session.user
	if "System Manager" in frappe.get_roles(user):
		return ""
	return (
		f"(`tabDocument AI Call Log`.reference_doctype = {frappe.db.escape(VERIFIER)} "
		f"AND `tabDocument AI Call Log`.reference_name IN (SELECT name FROM `tab{VERIFIER}` "
		f"WHERE owner = {frappe.db.escape(user)}))"
	)

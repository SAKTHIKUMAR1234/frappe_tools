import frappe 
from frappe.utils import escape_html
from frappe_tools.frappe_tools.doctype.scanned_document.scanned_document import get_print_html

from frappe.www.printview import get_print_style, trigger_print_script
no_cache = 1

def get_context(context):
	
	if not (frappe.form_dict.name or frappe.form_dict.doctype):
		return {
			"body": f"""
				<h1>Error</h1>
				<p>Parameters doctype and name required</p>
				<pre>{escape_html(frappe.as_json(frappe.form_dict, indent=2))}</pre>
				"""
		}
	doc = None
	doctype = None
	if frappe.form_dict.name :
		doc = frappe.form_dict.name 
	if frappe.form_dict.doctype :
		doctype = frappe.form_dict.doctype

	trigger_print = 1
	# if frappe.form_dict.trigger_print:
	# 	trigger_print = frappe.form_dict.trigger_print

	if doctype == 'Scanned Document':
		return get_scanned_document_print_detail(doc )
	

def get_scanned_document_print_detail(doc):
	html = get_print_html(doc)
	html += trigger_print_script
	return  {"body": html, "css": get_print_style(), "title": "Document Scan :" + doc }

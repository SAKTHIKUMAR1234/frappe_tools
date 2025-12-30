# Copyright (c) 2025, sakthi123msd@gmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.www.printview import get_print_style
from frappe.utils.pdf import get_pdf

class ScannedDocument(Document):
		
	def set_new_document_names(self):
		
		if self.flags.documents and len(self.flags.documents) > 0:
			filter = {
				"name" : ["in", self.flags.documents]
			}
			frappe.db.set_value("Scanned Document Detail", filter , "is_deleted", 0)
			frappe.db.set_value("Scanned Document Detail", filter, "scanner_document", self.name)

	def set_prev_documents_delete(self):
		if self.is_new():
			return

		docs = frappe.get_all(
			"Scanned Document Detail",
			filters={"scanner_document": self.name},
			pluck="name"
		)

		if not docs:
			return

		frappe.db.set_value(
			"Scanned Document Detail",
			{"name": ["in", docs]},
			"is_deleted",
			1
		)

	def on_trash(self):
		self.set_prev_documents_delete()

@frappe.whitelist()
def get_print_html(doc):
	from frappe_tools.api.doc_scanner import load_scanned_document_details

	template_path = "frappe_tools/frappe_tools/doctype/scanned_document/scanned_document.html"

	doc_details = load_scanned_document_details(doc)
	layout = frappe.get_doc("Document Scanner Layout", doc_details['layout'])
	print_data = {
		"layout" : layout.name,
		"sections" : get_section_details(layout, doc_details)
	}
	html = frappe.render_template(template_path, \
			{"data": print_data})
	return html

@frappe.whitelist()
def get_scan_pdf(doc):
	html = get_print_html(doc)
	pdf = get_pdf(
		html,
		options={
			"print-media-type": None,
			"disable-smart-shrinking": "",
			"margin-top": "8mm",
			"margin-bottom": "8mm",
			"margin-left": "8mm",
			"margin-right": "8mm",
		}
	)
	filename = f"Document_Scan_{doc}.pdf"
	frappe.local.response.filename = filename
	frappe.local.response.filecontent = pdf
	frappe.local.response.type = "download"

@frappe.whitelist()
def get_section_details(layout, doc_details):
	resp = {}
	for i in layout.get('layout_doctype_sections'):
		resp[i.get('title')] = {
			"section_type" : i.get('layout_type'),
			"images" : [] 
		}
	for i in doc_details['attachments']:
		resp[i.get('title')]['images'].append({
			"page_no" : i['page_no'],
			"attachment" : i['attachment'],
			"page_type" : i['page_type'],
		})
	
	return resp
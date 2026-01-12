# Copyright (c) 2025, sakthi123msd@gmail.com and contributors
# For license information, please see license.txt

import base64
import io
from PIL import Image
import requests
import frappe
from frappe.model.document import Document
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
def get_scan_pdf(name):
	html = get_print_html(name)

	pdf = get_pdf(
		html,
		options={
			 "page-size": "A4",
	"dpi": 96,
	"image-quality": 55,
	"disable-smart-shrinking": "",
	"print-media-type": "",
		}
	)

	frappe.local.response.filename = f"Document_Scan_{name}.pdf"
	frappe.local.response.filecontent = pdf
	frappe.local.response.type = "download"

def compress_external_image(url, max_width_px=1080, quality=80):
	"""
	max_width_px ≈ A4 width @ ~200 DPI
	"""
	print("SSS")
	resp = requests.get(url, timeout=10)
	resp.raise_for_status()

	img = Image.open(io.BytesIO(resp.content))

	if img.mode in ("RGBA", "P"):
		img = img.convert("RGB")

	w, h = img.size
	if w > max_width_px:
		ratio = max_width_px / float(w)
		img = img.resize((max_width_px, int(h * ratio)), Image.LANCZOS)

	buf = io.BytesIO()
	img.save(buf, format="JPEG", optimize=True, quality=quality)

	encoded = base64.b64encode(buf.getvalue()).decode()
	return f"data:image/jpeg;base64,{encoded}"

@frappe.whitelist()
def get_section_details(layout, doc_details):
	resp = {}
	for i in layout.get('layout_doctype_sections'):
		resp[i.get('title')] = {
			"section_type" : i.get('layout_type'),
			"images" : [] 
		}
	for i in doc_details['attachments']:
		if i['attachment'] :
			i['attachment'] = compress_external_image(i['attachment'])
		resp[i.get('title')]['images'].append({
			"page_no" : i['page_no'],
			"attachment" : i['attachment'],
			"page_type" : i['page_type'],
		})	
	return resp
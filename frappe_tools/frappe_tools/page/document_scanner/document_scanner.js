frappe.pages['document-scanner'].on_page_load = function(wrapper) {
}
frappe.pages['document-scanner'].refresh = function(wrapper) {
	var page = new frappe.frappe_tools.doc_scanner.ImageScanner({
		wrapper
	});
}


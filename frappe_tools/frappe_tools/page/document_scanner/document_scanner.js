frappe.pages['document-scanner'].on_page_load = function(wrapper) {
}
frappe.pages['document-scanner'].refresh = function(wrapper) {
	let route = frappe.get_route();
	if(route.length < 4){
		frappe.msgprint("Invalid Route");
		return;
	}
	let doctype = route[1];
	let scan_name = route[route.length - 1];
	let document_name = route.slice(2, route.length - 1).join('/');

	var page = new frappe.frappe_tools.doc_scanner.ImageScanner({
		wrapper,
		is_new : decodeURIComponent(scan_name) == 'new' ? true : false,
		document_name : decodeURIComponent(document_name),
		doctype : decodeURIComponent(doctype),
		scan_name : decodeURIComponent(scan_name)
	});
}


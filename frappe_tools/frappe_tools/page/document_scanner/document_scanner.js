frappe.pages['document-scanner'].on_page_load = function(wrapper) {
}
frappe.pages['document-scanner'].refresh = function(wrapper) {
	let route = frappe.get_route();
	if(route.length != 4){
		frappe.msgprint("Invalid Route");
		return;
	}
	var page = new frappe.frappe_tools.doc_scanner.ImageScanner({
		wrapper,
		is_new : route[3] == 'new' ? true : false,
		document_name : route[2],
		doctype : route[1],
		scan_name : route[3]
	});
}


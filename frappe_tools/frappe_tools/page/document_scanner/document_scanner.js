frappe.pages["document-scanner"].on_page_load = function (wrapper) {
//   setupDocumentScanViewButton(wrapper);
};
frappe.pages["document-scanner"].refresh = function (wrapper) {
  setupDocumentScanViewButton(wrapper);
};

function setupDocumentScanViewButton(wrapper) {
  let route = frappe.get_route();
  
  if (route.length < 2) {
    frappe.msgprint("Invalid Route");
    return;
  }

  let is_new = false;
  let doctype = null;
  let scan_name = null;
  let document_name = null;

  if (route[1] === 'new') {
    is_new = true;
    scan_name = 'new';
  } else if (route.length >= 4) {
    doctype = route[1];
    scan_name = route[route.length - 1];
    document_name = route.slice(2, route.length - 1).join("/");
    is_new = decodeURIComponent(scan_name) == "new" ? true : false;
  } else {
    frappe.msgprint("Invalid Route Format");
    return;
  }

  var page = new frappe.frappe_tools.doc_scanner.ImageScanner({
    wrapper,
    is_new: is_new,
    document_name: document_name ? decodeURIComponent(document_name) : null,
    doctype: doctype ? decodeURIComponent(doctype) : null,
    scan_name: scan_name ? decodeURIComponent(scan_name) : null,
  });
}

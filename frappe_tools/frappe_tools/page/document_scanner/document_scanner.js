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

  // Extraction mode: document-scanner/extract/<target_doctype>[/<extraction>]
  let extract = false;
  let target_doctype = null;
  let extraction = null;

  if (route[1] === "extract") {
    extract = true;
    target_doctype = route[2] ? decodeURIComponent(route[2]) : null;
    extraction = route[3] ? decodeURIComponent(route[3]) : null;
    if (!target_doctype) {
      frappe.msgprint("Missing target DocType for extraction");
      return;
    }
  } else if (route[1] === "new") {
    is_new = true;
    scan_name = "new";
  } else if (route.length >= 4) {
    doctype = route[1];
    scan_name = route[route.length - 1];
    document_name = route.slice(2, route.length - 1).join("/");
    is_new = decodeURIComponent(scan_name) == "new" ? true : false;
  } else {
    frappe.msgprint("Invalid Route Format");
    return;
  }

  new frappe.frappe_tools.doc_scanner.ImageScanner({
    wrapper,
    is_new: is_new,
    document_name: document_name ? decodeURIComponent(document_name) : null,
    doctype: doctype ? decodeURIComponent(doctype) : null,
    scan_name: scan_name ? decodeURIComponent(scan_name) : null,
    extract: extract,
    target_doctype: target_doctype,
    extraction: extraction,
  });
}

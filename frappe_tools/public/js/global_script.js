$(document).on("app_ready", function () {
  window.doc_scanner_allowed_doctypes = [];
  let allowed_loaded = false;

  if (allowed_loaded) return;
  allowed_loaded = true;

  frappe.call({
    method: "frappe_tools.api.doc_scanner.get_docscanner_allowed_doctypes",
    callback(r) {
      window.doc_scanner_allowed_doctypes = r.message || [];
      $.each(window.doc_scanner_allowed_doctypes, function (i, doctype) {
        frappe.ui.form.on(doctype, {
          refresh: function (frm) {
            addDocScannerButton(frm);
          },
        });
      });
    },
  });
});

function addDocScannerButton(frm) {
  frm.add_custom_button(__("View Scanned Documents"), () => {
    const d = new frappe.ui.Dialog({
      title: __("Scanned Documents"),
      size: "large",
      fields: [{ fieldtype: "HTML", fieldname: "scanned_docs_html" }],
      primary_action_label: __("Close"),
      primary_action() {
        d.hide();
      },
    });

    d.show();

    new frappe.frappe_tools.doc_scanner.DocumentListViewer({
      wrapper: d.fields_dict.scanned_docs_html.wrapper,
      doctype: frm.doctype,
      docname: frm.doc.name,
    });
  });
}

// "Create from Scan" — list-view entry point for AI document extraction.
// Shown on the List View of any DocType that has at least one enabled Rule Book.
$(document).on("app_ready", function () {
  frappe.call({
    method: "frappe_tools.api.doc_extract.get_extractable_doctypes",
    callback(r) {
      (r.message || []).forEach(function (entry) {
        const dt = entry.doctype;
        const existing = frappe.listview_settings[dt] || {};
        const prev_onload = existing.onload;
        frappe.listview_settings[dt] = Object.assign(existing, {
          onload(listview) {
            if (typeof prev_onload === "function") prev_onload(listview);
            listview.page.add_inner_button(__("Create from Scan"), function () {
              frappe.set_route("document-scanner", "extract", dt);
            });
          },
        });
      });
    },
  });
});

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
// Injected on route change (not via listview_settings) so it survives DocTypes
// that ship their own listview_settings and clobber it — e.g. ERPNext's
// Purchase Invoice does `frappe.listview_settings["Purchase Invoice"] = {...}`.
$(document).on("app_ready", function () {
  frappe.call({
    method: "frappe_tools.api.doc_extract.get_extractable_doctypes",
    callback(r) {
      const extractable = {};
      (r.message || []).forEach(function (entry) {
        extractable[entry.doctype] = true;
      });
      window._frappe_tools_extractable = extractable;

      frappe.router.on("change", function () {
        tryAddScanButton(extractable);
      });
      tryAddScanButton(extractable); // if a list is already open at boot
    },
  });
});

function tryAddScanButton(extractable, attempt) {
  attempt = attempt || 0;
  const route = frappe.get_route();
  if (!route || route[0] !== "List") return;

  const doctype = route[1];
  if (!extractable[doctype]) return;

  const lv = window.cur_list;
  if (!lv || lv.doctype !== doctype || !lv.page) {
    // List view not constructed yet — retry briefly (~1.5s total).
    if (attempt < 10) {
      setTimeout(function () {
        tryAddScanButton(extractable, attempt + 1);
      }, 150);
    }
    return;
  }

  if (lv.__frappe_tools_scan_btn) return; // already added to this instance
  lv.__frappe_tools_scan_btn = true;
  lv.page.add_inner_button(__("Create from Scan"), function () {
    frappe.set_route("document-scanner", "extract", doctype);
  });
}

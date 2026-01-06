(function () {
  window.doc_scanner_allowed_doctypes = [];
  let allowed_loaded = false;

  $(document).ready(function () {
    if (allowed_loaded) return;
    allowed_loaded = true;

    frappe.call({
      method: "frappe_tools.api.doc_scanner.get_docscanner_allowed_doctypes",
      callback(r) {
        window.doc_scanner_allowed_doctypes = r.message || [];
        tryApply();
      },
    });
  });

  frappe.router.on("change", () => {
    tryApply();
  });

  function tryApply(retries = 12) {
    if (!window.cur_frm || !cur_frm.doctype || !cur_frm.doc) {
      if (retries <= 0) return;
      return setTimeout(() => tryApply(retries - 1), 200);
    }

    if (!window.doc_scanner_allowed_doctypes.length) {
      if (retries <= 0) return;
      return setTimeout(() => tryApply(retries - 1), 200);
    }

    maybeAddDocScannerButton(cur_frm);
  }

  function maybeAddDocScannerButton(frm) {
    if (!window.doc_scanner_allowed_doctypes.includes(frm.doctype)) {
      return;
    }
    setTimeout(() => {
      addDocScannerButton(frm);
    }, 1000);
  }

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
})();

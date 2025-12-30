$(window).on("page-change", page_changed);

var doc_scanner_allowed_doctypes = [];
var is_first_load = true;

function page_changed(event) {
  frappe.after_ajax(async function () {
    var route = await frappe.get_route();
    if (route[0] == "Form") {
      if (doc_scanner_allowed_doctypes.length === 0 && is_first_load) {
        is_first_load = false;
        frappe.call({
          method:
            "frappe_tools.api.doc_scanner.get_docscanner_allowed_doctypes",
          callback: function (r) {
            doc_scanner_allowed_doctypes = r.message;
            execute_form_script(route);
          },
        });
      } else {
        execute_form_script(route);
      }
    }
  });
}

function execute_form_script(route) {
  if (doc_scanner_allowed_doctypes.includes(route[1])) {
    cur_frm.add_custom_button(__("View Scanned Documents"), function () {
      let d = new frappe.ui.Dialog({
        title: "Scanned Documents",
        size: "large",
        fields: [
          {
            fieldtype: "HTML",
            fieldname: "scanned_docs_html",
          },
        ],
        primary_action_label: "Close",
        primary_action: function () {
          d.hide();
        },
      });
      d.show();
      $(d.fields_dict["scanned_docs_html"]).html("");
      d.scanned_docs_html = new frappe.frappe_tools.doc_scanner.DocumentListViewer({
        wrapper: d.fields_dict["scanned_docs_html"].wrapper,
        doctype: cur_frm.doc.doctype,
        docname: cur_frm.doc.name,
      });
    });
  }
}

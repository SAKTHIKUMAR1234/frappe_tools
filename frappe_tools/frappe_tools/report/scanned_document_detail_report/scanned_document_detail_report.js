frappe.query_reports["Scanned Document Detail Report"] = {
  disable_auto_run: true,


  onload: function (report) {

    frappe.call({
      method:
        "frappe_tools.frappe_tools.report.scanned_document_detail_report.scanned_document_detail_report.get_document_types",
      callback: function (r) {
        if (r.message) {
          report.set_filter_value("document_type", null);
          report.filters[0].df.options = r.message.join("\n");
          report.filters[0].refresh();
        }
      },
    });
  },

  filters: [
    {
      fieldname: "document_type",
      label: __("Document Type"),
      fieldtype: "Select",
      reqd: 1,
      options: "",
    },
    {
      fieldname: "start_date",
      label: __("Document Start Date (creation)"),
      fieldtype: "Date",
      reqd: 1,
      default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
    },
    {
      fieldname: "end_date",
      label: __("Document End Date (creation)"),
      fieldtype: "Date",
      reqd: 1,
      default: frappe.datetime.get_today(),
    },
  ],
};

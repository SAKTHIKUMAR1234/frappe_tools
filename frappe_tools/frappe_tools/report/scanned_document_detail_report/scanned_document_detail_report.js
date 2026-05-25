const SCANNER_REPORT_METHOD =
  "frappe_tools.frappe_tools.report.scanned_document_detail_report.scanned_document_detail_report";

// fieldname -> { field, type, default } for the currently selected document type
let DATE_FIELD_CONFIG = {};

frappe.query_reports["Scanned Document Detail Report"] = {
  disable_auto_run: true,

  onload: function (report) {
    frappe.call({
      method: SCANNER_REPORT_METHOD + ".get_document_types",
      callback: function (r) {
        if (r.message) {
          report.set_filter_value("document_type", null);
          report.filters[0].df.options = r.message.join("\n");
          report.filters[0].refresh();
        }
      },
    });
    // keep the date range hidden until a field is chosen
    toggle_date_filters();
  },

  filters: [
    {
      fieldname: "document_type",
      label: __("Document Type"),
      fieldtype: "Select",
      reqd: 1,
      options: "",
      on_change: function () {
        load_filter_fields();
      },
    },
    {
      fieldname: "filter_field",
      label: __("Filter By Field"),
      fieldtype: "Select",
      reqd: 1,
      options: "",
      on_change: function () {
        toggle_date_filters();
      },
    },
    {
      fieldname: "date_start",
      label: __("Start Date"),
      fieldtype: "Date",
    },
    {
      fieldname: "date_end",
      label: __("End Date"),
      fieldtype: "Date",
    },
    {
      fieldname: "datetime_start",
      label: __("Start Date/Time"),
      fieldtype: "Datetime",
    },
    {
      fieldname: "datetime_end",
      label: __("End Date/Time"),
      fieldtype: "Datetime",
    },
  ],
};

// Pull the configured date fields for the selected document type and
// populate the "Filter By Field" dropdown.
function load_filter_fields() {
  const report = frappe.query_report;
  const document_type = report.get_filter_value("document_type");
  const field_filter = report.get_filter("filter_field");

  DATE_FIELD_CONFIG = {};

  if (!document_type) {
    field_filter.df.options = "";
    field_filter.set_value("");
    field_filter.refresh();
    toggle_date_filters();
    return;
  }

  frappe.call({
    method: SCANNER_REPORT_METHOD + ".get_report_date_fields",
    args: { document_type: document_type },
    callback: function (r) {
      const fields = r.message || [];
      fields.forEach(function (f) {
        DATE_FIELD_CONFIG[f.field] = f;
      });
      // show the label in the dropdown, keep the fieldname as the stored value
      field_filter.df.options = fields.map(function (f) {
        return { value: f.field, label: f.label || f.field };
      });
      // auto-select the first configured field
      field_filter.set_value(fields.length ? fields[0].field : "");
      field_filter.refresh();
      toggle_date_filters();
    },
  });
}

// Show the Date or the Datetime range based on the selected field's
// configured type, hide the other, and apply any default.
function toggle_date_filters() {
  const report = frappe.query_report;
  const date_start = report.get_filter("date_start");
  const date_end = report.get_filter("date_end");
  const dt_start = report.get_filter("datetime_start");
  const dt_end = report.get_filter("datetime_end");

  if (!date_start || !dt_start) return;

  const selected = report.get_filter_value("filter_field");
  const conf = DATE_FIELD_CONFIG[selected] || {};
  const is_datetime = conf.type === "Datetime";
  const has_field = !!selected;

  date_start.$wrapper.toggle(has_field && !is_datetime);
  date_end.$wrapper.toggle(has_field && !is_datetime);
  dt_start.$wrapper.toggle(has_field && is_datetime);
  dt_end.$wrapper.toggle(has_field && is_datetime);

  if (has_field && conf.default) {
    const start = is_datetime ? dt_start : date_start;
    if (!start.get_value()) {
      start.set_value(conf.default);
    }
  }
}

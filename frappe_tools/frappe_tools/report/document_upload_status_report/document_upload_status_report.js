// Copyright (c) 2026, sakthi123msd@gmail.com and contributors
// For license information, please see license.txt

const UPLOAD_REPORT_METHOD =
  "frappe_tools.frappe_tools.report.scanned_document_detail_report.scanned_document_detail_report";

// fieldname -> { field, type, default } for the currently selected document type
let UPLOAD_DATE_FIELD_CONFIG = {};

frappe.query_reports["Document Upload Status Report"] = {
  disable_auto_run: true,
  onload: function (report) {
    frappe.call({
      method: UPLOAD_REPORT_METHOD + ".get_document_types",
      callback: function (r) {
        if (r.message) {
          report.set_filter_value("document_type", null);
          report.filters[0].df.options = r.message.join("\n");
          report.filters[0].refresh();
        }
      },
    });
    // keep the date range hidden until a field is chosen
    upload_toggle_date_filters();
  },

  filters: [
    {
      fieldname: "document_type",
      label: __("Document Type"),
      fieldtype: "Select",
      reqd: 1,
      options: "",
      on_change: function () {
        upload_load_filter_fields();
      },
    },
    {
      fieldname: "filter_field",
      label: __("Filter By Field"),
      fieldtype: "Select",
      reqd: 1,
      options: "",
      on_change: function () {
        upload_toggle_date_filters();
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
    {
      fieldname: "scan_status",
      label: __("Scan Status"),
      fieldtype: "Select",
      options: ["", "Scanned", "Not Scanned"].join("\n"),
    },
  ],

  formatter: function (value, row, column, data, default_formatter) {
    value = default_formatter(value, row, column, data);
    if (column.fieldname === "is_scanned") {
      if (value) {
        return `
          <span class="indicator green">
            Scanned
          </span>
        `;
      }
      return `
        <span class="indicator red">
          Not Scanned
        </span>
      `;
    }
    return value;
  },
};

// Pull the configured date fields for the selected document type and
// populate the "Filter By Field" dropdown.
function upload_load_filter_fields() {
  const report = frappe.query_report;
  const document_type = report.get_filter_value("document_type");
  const field_filter = report.get_filter("filter_field");

  UPLOAD_DATE_FIELD_CONFIG = {};

  if (!document_type) {
    field_filter.df.options = "";
    field_filter.set_value("");
    field_filter.refresh();
    upload_toggle_date_filters();
    return;
  }

  frappe.call({
    method: UPLOAD_REPORT_METHOD + ".get_report_date_fields",
    args: { document_type: document_type },
    callback: function (r) {
      const fields = r.message || [];
      fields.forEach(function (f) {
        UPLOAD_DATE_FIELD_CONFIG[f.field] = f;
      });
      // show the label in the dropdown, keep the fieldname as the stored value
      field_filter.df.options = fields.map(function (f) {
        return { value: f.field, label: f.label || f.field };
      });
      // auto-select the first configured field
      field_filter.set_value(fields.length ? fields[0].field : "");
      field_filter.refresh();
      upload_toggle_date_filters();
    },
  });
}

// Show the Date or the Datetime range based on the selected field's
// configured type, hide the other, and apply any default.
function upload_toggle_date_filters() {
  const report = frappe.query_report;
  const date_start = report.get_filter("date_start");
  const date_end = report.get_filter("date_end");
  const dt_start = report.get_filter("datetime_start");
  const dt_end = report.get_filter("datetime_end");

  if (!date_start || !dt_start) return;

  const selected = report.get_filter_value("filter_field");
  const conf = UPLOAD_DATE_FIELD_CONFIG[selected] || {};
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

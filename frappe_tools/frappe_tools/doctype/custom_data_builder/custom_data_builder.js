// Copyright (c) 2025, sakthi123msd@gmail.com and contributors
// For license information, please see license.txt

var filter_group = undefined;
var onload_set = false;

frappe.ui.form.on("Custom Data Builder", {
  refresh(frm) {
    setup_filters(frm);
  },
  onload(frm) {
    setup_filters(frm);
  },
  apply_filter(frm) {
    frm.dirty();
    _apply_filter(frm);
  },
  validate(frm) {
    if (frm.doc.resource_document_type == "Doctype" && frm.doc._doctype && filter_group) {
      let filters = filter_group.get_filters();
      frm.doc.filter_json = JSON.stringify(filters);
    } else {
      frm.doc.filter_json = null;
    }
  },
});

function _apply_filter(frm) {
  let filters = filter_group.get_filters();
  fetchDataWithFilters(frm, filters);
}

function fetchDataWithFilters(frm, filters) {
  frappe.call({
    method:
      "frappe_tools.frappe_tools.doctype.data_builder.data_builder.get_list_details",
    args: {
      doctype: frm.doc._doctype,
      filters: JSON.stringify(filters),
      limit: 10,
    },
    callback: (r) => {
      if (!r.message) {
        frappe.msgprint("No data found");
        return;
      }

      render_preview_table(frm, r.message.data, r.message.total_count);
    },
  });
}

function getDataWithExcel(frm) {
  frappe.call({
    method:
      "frappe_tools.frappe_tools.doctype.data_builder.data_builder.get_document_uploaded_values",
    args: {
      doc_name: frm.doc.name,
    },
    callback: (r) => {
      console.log(r);
    },
  });
}

function render_preview_table(frm, data, total_count) {
  const wrapper = frm.fields_dict["data_viewer_html"].$wrapper;
  wrapper.empty();

  if (!data || data.length === 0) {
    wrapper.html("<p class='text-muted'>No data found.</p>");
    return;
  }

  let html = `
    <div class="data-preview-container">

      <div class="data-preview-header">
        <div>
          <b>Showing ${data.length} of ${total_count} records</b>
        </div>

        <div>
          <button class="btn btn-sm btn-primary" id="data_download">
            Download Excel
          </button>
        </div>
      </div>

      <div class="data-preview-table-wrapper">
        <table class="table table-bordered table-hover table-sm data-preview-table">
          <thead>
            <tr>
  `;

  Object.keys(data[0]).forEach((key) => {
    html += `<th>${frappe.utils.escape_html(key)}</th>`;
  });

  html += `</tr></thead><tbody>`;

  data.forEach((row) => {
    html += "<tr>";
    Object.values(row).forEach((val) => {
      html += `<td>${frappe.utils.escape_html(val ?? "")}</td>`;
    });
    html += "</tr>";
  });

  html += `
          </tbody>
        </table>
      </div>
    </div>
  `;

  wrapper.html(html);
  inject_data_preview_styles();
  let ele = document.getElementById("data_download");
  if (ele) {
    ele.addEventListener("click", () => {
      download_excel(frm.doc.name);
    });
  }
}

function inject_data_preview_styles() {
  if (document.getElementById("data-preview-style")) return;

  const style = document.createElement("style");
  style.id = "data-preview-style";
  style.innerHTML = `
    .data-preview-container {
      border: 1px solid #d1d8dd;
      border-radius: 6px;
      background: #ffffff;
      padding: 10px;
      margin-top: 10px;
    }

    .data-preview-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 8px;
    }

    .data-preview-table-wrapper {
      max-height: 420px;     /* Vertical scroll */
      overflow-y: auto;
      overflow-x: auto;     /* Horizontal scroll */
      border: 1px solid #e5e7eb;
    }

    .data-preview-table {
      margin-bottom: 0;
      white-space: nowrap;  /* Prevent column wrapping */
      font-size: 12.5px;
    }

    .data-preview-table th {
      position: sticky;
      top: 0;
      background: #f8f9fa;
      z-index: 2;
      font-weight: 600;
    }

    .data-preview-table td {
      vertical-align: middle;
    }
  `;

  document.head.appendChild(style);
}

function download_excel(docname) {
  window.open(
    `/api/method/frappe_tools.frappe_tools.doctype.data_builder.data_builder.download_excel?name=${docname}`
  );
}

function setup_filters(frm) {
  if (frm.is_new()) {
    return;
  }
  if (onload_set) {
    return;
  }

  const wrapper = frm.fields_dict["doctype_filter_group_html"].$wrapper;
  wrapper.empty();
  if (
    frm.doc.resource_document_type == "Excel Upload" &&
    frm.doc.resouce_document
  ) {
    getDataWithExcel(frm);
  }

  if (frm.doc.resource_document_type == "Doctype" && frm.doc._doctype) {
    let filters = [];
    if (frm.doc.filter_json) {
      try {
        filters = JSON.parse(frm.doc.filter_json);
      } catch (e) {
        console.error("Invalid filter_json:", e);
      }
    }

    frappe.model.with_doctype(frm.doc._doctype, () => {
      filter_group = new frappe.ui.FilterGroup({
        parent: wrapper,
        doctype: frm.doc._doctype,
      });

      filters.forEach((f) => {
        filter_group.push_new_filter(f, true);
      });
      _apply_filter(frm);

      frm.refresh_field("doctype_filter_group_html");
    });
  }
  onload_set = true;
}

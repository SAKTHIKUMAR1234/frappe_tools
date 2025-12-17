// Copyright (c) 2025, sakthi123msd@gmail.com and contributors
// For license information, please see license.txt

var filter_group = undefined;
var onload_set = false;

frappe.ui.form.on("Custom Data Builder", {
  refresh(frm) {
    filter_group = undefined;
    onload_set = false;
    setup_filters(frm);
    setup_action_btn(frm);
  },
  onload(frm) {
    setup_filters(frm);
  },
  apply_filter(frm) {
    frm.dirty();
    _apply_filter(frm);
  },
  validate(frm) {
    if (
      frm.doc.resource_document_type == "Doctype" &&
      frm.doc._doctype &&
      filter_group
    ) {
      let filters = filter_group.get_filters();
      frm.doc.filter_json = JSON.stringify(filters);
    } else {
      frm.doc.filter_json = null;
    }
  },
});

function setup_action_btn(frm) {
  if (frm.doc.docstatus == 1) {
    frm.add_custom_button("Share", () => {
      show_share_dialog(frm);
    });
  }

  if (!frm.is_new() && frm.doc.resource_document) {
    frm.add_custom_button("Preview", () => {
      show_preview_dialog(frm);
    });
  }
}

function show_preview_dialog(frm) {
  frappe.call({
    method: "frappe_tools.frappe_tools.doctype.custom_data_builder.custom_data_builder.get_preview_content",
    args: {
      doc: frm.doc.name
    },
    freeze: true,
    freeze_message: "Generating Preview...",
    callback(r) {
      if (r.message) {
        let content = r.message;
        let attachment_html = "";
        
        if (content.attachment_url) {
            attachment_html = `
                <div style="margin-top: 15px; padding-top: 15px; border-top: 1px solid #d1d8dd;">
                    <strong>Attachment:</strong><br>
                    <a href="${content.attachment_url}" target="_blank" class="btn btn-default btn-sm" style="margin-top:5px;">
                        Download PDF Preview
                    </a>
                </div>
            `;
        }
        
        let d = new frappe.ui.Dialog({
          title: 'Preview: ' + content.subject,
          fields: [
            {
              fieldname: 'html_preview',
              fieldtype: 'HTML',
              options: `<div style="padding:15px; border:1px solid #d1d8dd; background:#f9fafb; border-radius:4px;">
                          ${content.html_body}
                        </div>
                        ${attachment_html}`
            }
          ],
          primary_action_label: 'Close',
          primary_action(values) {
            d.hide();
          }
        });

        d.show();
        d.$wrapper.find('.modal-dialog').css("max-width", "800px");
      }
    }
  });
}

function show_share_dialog(frm) {
  frappe.confirm(
    "Are you sure you want to send emails to all recipients based on the uploaded data?",
    () => {
      frappe.call({
        method:
          "frappe_tools.frappe_tools.doctype.custom_data_builder.custom_data_builder.send_email",
        args: {
          doc: frm.doc.name,
        },
        callback(r) {
          frappe.show_alert("Email Operations Started");
        },
      });
    }
  );
}

function is_valid_email(email) {
  if (!email) return true;
  const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return regex.test(email);
}

function _apply_filter(frm) {
  let filters = filter_group.get_filters();
  fetchDataWithFilters(frm, filters);
}

function fetchDataWithFilters(frm, filters) {
  frappe.call({
    method:
      "frappe_tools.frappe_tools.doctype.custom_data_builder.custom_data_builder.get_list_details",
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
      "frappe_tools.frappe_tools.doctype.custom_data_builder.custom_data_builder.get_document_uploaded_values",
    args: {
      doc_name: frm.doc.name,
    },
    callback: (r) => {
      render_preview_table(frm, r.message["data"], r.message["total_count"]);
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
  
  let row_headers = Object.keys(data[0]);
  row_headers.forEach((key) => {
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
    `/api/method/frappe_tools.frappe_tools.doctype.custom_data_builder.custom_data_builder.download_excel?name=${docname}`
  );
}

function setup_filters(frm) {
  const _wrapper = frm.fields_dict["data_viewer_html"].$wrapper;
  _wrapper.empty();

  const wrapper = frm.fields_dict["doctype_filter_group_html"].$wrapper;
  wrapper.empty();

  if (frm.is_new()) {
    return;
  }
  if (onload_set) {
    return;
  }
  
  if (
    frm.doc.resource_document_type == "Excel Upload" &&
    frm.doc.resource_document
  ) {
    getDataWithExcel(frm);
  }

  if (
    frm.doc.resource_document_type == "Doctype" &&
    frm.doc._doctype &&
    frm.doc.docstatus == 0
  ) {
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

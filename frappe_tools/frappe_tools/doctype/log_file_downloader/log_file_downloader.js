// Copyright (c) 2025, sakthi123msd@gmail.com and contributors
// For license information, please see license.txt

frappe.ui.form.on("Log File Downloader", {
  refresh(frm) {
    frm.disable_save();
    fetchAndMakeAvailableLogFiles(frm);
  },
});

async function fetchAndMakeAvailableLogFiles(frm) {
  frappe.call({
    method:
      "frappe_tools.frappe_tools.doctype.log_file_downloader.log_file_downloader.get_logs_namspaces",
    callback: function (response) {
      file_dict = response.message;
      create_and_make_file_table(frm, file_dict);
    },
    freeze: true,
    freeze_msg: "Fetching Log Files Namespaces",
  });
}

function create_and_make_file_table(frm, file_dict) {
  frm.fields_dict["file_select_html"].$wrapper.html("");
  let table_html = `
    <table class="table table-bordered">
        <thead>
            <tr class="thead-dark">
                <th>File Name</th>
                <th>Count</th>
                <th>Download</th>
            </tr>
        </thead>
        <tbody>
`;
  let keys = Object.keys(file_dict);
  for (let i = 0; i < keys.length; i++) {
    let key = file_dict[keys[i]];
    table_html += `
            <tr>
                <td>${keys[i]}</td>
                <td>${key}</td>
                <td>
                    <button class="btn btn-primary download-btn" data-file="${keys[i]}">Download</button>
                </td>
            </tr>
        `;
  }

  table_html += `</tbody></table>`;

  frm.fields_dict["file_select_html"].$wrapper.html(table_html);

  let eles = document.getElementsByClassName("download-btn");
  for (let i = 0; i < eles.length; i++) {
    let ele = eles[i];
    ele.addEventListener("click", () => {
      download_log_file(ele.getAttribute("data-file"));
    });
  }
}

function download_log_file(file_name) {
  let ele = document.createElement("a");
  ele.href =
    window.location.origin +
    `/api/method/frappe_tools.frappe_tools.doctype.log_file_downloader.log_file_downloader.download_log_zips?file_name=${file_name}`;
  ele.target = "_blank";
  ele.click();
  ele.remove();
}

// Copyright (c) 2026, sakthi123msd@gmail.com and contributors
// For license information, please see license.txt

frappe.ui.form.on("Scanned Document AI Verifier", {
	setup(frm) {
		frm.set_query("reference_doctype", () => ({
			filters: { istable: 0, issingle: 0, is_virtual: 0 },
		}));
	},

	refresh(frm) {
		remove_progress_listener(frm);
		listen_for_progress(frm);
		show_progress(frm, frm.doc);
		const active = ["Queued", "Running"].includes(frm.doc.status);
		for (const field of ["reference_doctype", "prompt", "max_documents"]) {
			frm.set_df_property(field, "read_only", active);
		}
		if (active) frm.disable_save();
		else frm.enable_save();
		if (!active && frm.perm[0]?.write) {
			frm.add_custom_button(__("Run"), async () => {
				if (frm.scan_verifier_starting) return;
				frm.scan_verifier_starting = true;
				try {
					if (frm.is_new() || frm.is_dirty()) await frm.save();
					await frappe.call({
						method: "frappe_tools.frappe_tools.doctype.scanned_document_ai_verifier.scanned_document_ai_verifier.run_verifier",
						args: { name: frm.doc.name },
						freeze: true,
						freeze_message: __("Queuing verification..."),
					});
					await frm.reload_doc();
				} finally {
					frm.scan_verifier_starting = false;
				}
			});
		}
		if (frm.doc.result_file) {
			frm.add_custom_button(__("Download Excel"), () => {
				const link = document.createElement("a");
				link.href = frm.doc.result_file;
				link.download = `${frm.doc.name}.xlsx`;
				document.body.appendChild(link);
				link.click();
				link.remove();
			});
		}
		return render_filters(frm, active);
	},

	async reference_doctype(frm) {
		frm.scan_filter_group = null;
		await frm.set_value("filters_data", "[]");
		await render_filters(frm, false);
	},

	validate(frm) {
		if (frm.scan_filter_group) {
			frm.doc.filters_data = JSON.stringify(frm.scan_filter_group.get_filters());
		}
	},

	on_hide(frm) {
		remove_progress_listener(frm);
		frm.scan_filter_request = null;
	},
});

async function render_filters(frm, active) {
	const request = {};
	frm.scan_filter_request = request;
	const doctype = frm.doc.reference_doctype;
	const filters = JSON.parse(frm.doc.filters_data || "[]");
	const wrapper = frm.get_field("filter_html").$wrapper;
	wrapper.empty();
	frm.scan_filter_group = null;
	if (!doctype) return;
	await frappe.model.with_doctype(doctype);
	if (frm.scan_filter_request !== request) return;
	let initializing = true;
	const filter_group = new frappe.ui.FilterGroup({
		parent: wrapper,
		doctype,
		on_change: () => {
			if (initializing || active || frm.scan_filter_request !== request) return;
			frm.set_value("filters_data", JSON.stringify(filter_group.get_filters()));
		},
	});
	for (const filter of filters) {
		await filter_group.push_new_filter(filter);
		if (frm.scan_filter_request !== request) return;
	}
	filter_group.toggle_empty_filters(!filters.length);
	frm.scan_filter_group = filter_group;
	initializing = false;
	wrapper.prop("inert", active);
}

function listen_for_progress(frm) {
	if (frm.is_new()) return;
	const name = frm.doc.name;
	frm.scan_progress_listener = (data) => {
		if (data.name !== name || frm.doc.name !== name) return;
		show_progress(frm, data);
		if (["Completed", "Failed"].includes(data.status)) frm.reload_doc();
	};
	frappe.realtime.on("scan_ai_verifier_progress", frm.scan_progress_listener);
}

function remove_progress_listener(frm) {
	if (!frm.scan_progress_listener) return;
	frappe.realtime.off("scan_ai_verifier_progress", frm.scan_progress_listener);
	frm.scan_progress_listener = null;
}

function show_progress(frm, data) {
	const total = data.total_documents || 0;
	const processed = data.processed_documents || 0;
	if (["Queued", "Running"].includes(data.status)) {
		frm.dashboard.show_progress(
			__("Verification"),
			total ? Math.min(100, (processed / total) * 100) : 0,
			__("{0} / {1} documents; {2} failed", [processed, total, data.failed_documents || 0])
		);
	} else {
		frm.dashboard.hide_progress();
	}
}

// Copyright (c) 2025, sakthi123msd@gmail.com and contributors
// For license information, please see license.txt

frappe.ui.form.on("Scanned Document", {
	async refresh(frm) {
		if (frm.is_new() || frm.doc.ai_reviewed) return;
		const method = "frappe_tools.scan_categorization.categorize.";
		const { message: allowed } = await frappe.call({
			method: method + "can_check",
			type: "GET",
			args: { name: frm.doc.name },
		});
		if (!allowed || frm.doc.ai_reviewed) return;
		if (frm.doc.ai_check_status !== "Queued") {
			frm.add_custom_button(__("Run AI check"), async () => {
				if (frm.is_dirty()) await frm.save();
				const { message } = await frappe.call({
					method: method + "run_ai_check",
					args: { name: frm.doc.name },
					freeze: true,
				});
				frappe.show_alert(message.queued ? __("AI check queued") : __("No scan queued"));
				await frm.reload_doc();
			});
		}
		frm.add_custom_button(__("Mark reviewed"), async () => {
			if (frm.is_dirty()) await frm.save();
			await frappe.call({
				method: method + "mark_reviewed",
				args: { name: frm.doc.name },
				freeze: true,
			});
			await frm.reload_doc();
		});
	},
});

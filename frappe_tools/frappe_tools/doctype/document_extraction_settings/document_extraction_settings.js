frappe.ui.form.on("Document Extraction Settings", {
	refresh(frm) {
		if (!frappe.user.has_role("System Manager")) return;
		frm.add_custom_button(__("Load default prompts"), () => load_default_prompts(frm));
	},
});

async function load_default_prompts(frm) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Save the settings before you load the default prompts."));
		return;
	}
	const filled = await frappe.xcall(
		"frappe_tools.frappe_tools.doctype.document_extraction_settings.document_extraction_settings.load_default_prompts",
	);
	await frm.reload_doc();
	frappe.show_alert({
		message: filled.length ? __("Loaded {0} default prompts", [filled.length]) : __("All prompts are already set"),
		indicator: "green",
	});
}

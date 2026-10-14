frappe.ui.form.on("AI Provider Account", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.provider !== "OpenAI Codex" || !frappe.user.has_role("System Manager")) return;
		frm.add_custom_button(__("Connect ChatGPT account"), () => connect_chatgpt(frm));
		frm.add_custom_button(__("Paste Codex auth.json"), () => import_auth(frm));
	},
});

async function connect_chatgpt(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Connect ChatGPT account"),
		fields: [
			{ fieldname: "verification_url", fieldtype: "Data", label: __("Open this URL"), read_only: 1 },
			{ fieldname: "user_code", fieldtype: "Data", label: __("Enter this code"), read_only: 1 },
			{ fieldtype: "HTML", options: __("Complete login in your browser within 15 minutes. This form updates when login is complete.") },
		],
	});
	const listener = (event) => {
		if (event.name !== frm.doc.name) return;
		dialog.hide();
		frm.reload_doc();
		frappe.msgprint(event.success ? __("ChatGPT account connected") : event.error);
	};
	frappe.realtime.on("ai_provider_login", listener);
	dialog.onhide = () => frappe.realtime.off("ai_provider_login", listener);
	try {
		const result = await frappe.xcall("frappe_tools.ai.accounts.start_device_login", { name: frm.doc.name });
		dialog.set_values(result);
		dialog.show();
	} catch (error) {
		frappe.realtime.off("ai_provider_login", listener);
		throw error;
	}
}

function import_auth(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Paste Codex auth.json"),
		fields: [{ fieldname: "auth_json", fieldtype: "Code", options: "JSON", label: __("Contents of ~/.codex/auth.json"), reqd: 1,
			description: __("The Codex CLI that uses this login can be logged out when the site refreshes it. Prefer Connect ChatGPT account.") }],
		primary_action_label: __("Connect"),
		async primary_action(values) {
			await frappe.xcall("frappe_tools.ai.accounts.import_auth_json", { name: frm.doc.name, auth_json: values.auth_json });
			dialog.set_value("auth_json", "");
			dialog.hide();
			frm.reload_doc();
		},
	});
	dialog.onhide = () => dialog.set_value("auth_json", "");
	dialog.show();
}

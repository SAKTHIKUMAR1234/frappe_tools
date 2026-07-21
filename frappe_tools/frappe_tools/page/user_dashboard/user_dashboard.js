// User Dashboard — a logged-in user's list of Custom User Dashboards shared with
// them. Clicking one opens its sandboxed render at /user-dashboard?name=<route>.

frappe.pages["user-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("User Dashboard"),
		single_column: true,
	});
	const $body = $(page.body);
	page.set_secondary_action(__("Refresh"), () => load(), "refresh");

	function load() {
		frappe.call("frappe_tools.frappe_tools.doctype.custom_user_dashboard.custom_user_dashboard.get_my_dashboards")
			.then((r) => render(r.message || []));
	}

	function render(dashboards) {
		if (!dashboards.length) {
			$body.html(`<div class="text-muted" style="padding:60px;text-align:center;">
				${__("No dashboards have been shared with you yet.")}</div>`);
			return;
		}
		$body.html(`<div class="cud-list" style="display:flex;flex-direction:column;gap:8px;max-width:720px;">
			${dashboards.map((d) => {
				const tag = d.require_auth
					? `<span class="indicator-pill blue">${__("Restricted")}</span>`
					: `<span class="indicator-pill green">${__("Public")}</span>`;
				return `<a class="cud-card" href="/user-dashboard?name=${encodeURIComponent(d.route || d.name)}" target="_blank"
						style="display:flex;align-items:center;justify-content:space-between;gap:10px;
						padding:12px 14px;border:1px solid var(--border-color);border-radius:8px;
						text-decoration:none;color:inherit;background:var(--card-bg);">
					<div>
						<div style="font-weight:600;font-size:13.5px;">${frappe.utils.escape_html(d.title || d.name)}</div>
						<div class="text-muted" style="font-size:11.5px;">${frappe.utils.escape_html(d.owner || "")}</div>
					</div>
					${tag}
				</a>`;
			}).join("")}
		</div>`);
	}

	load();
};

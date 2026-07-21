// AI Pages — a logged-in user's list of AI Bot Pages shared with them.
// Clicking one opens its sandboxed render at /ai-page?name=<route>.

frappe.pages["ai-pages"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("AI Pages"),
		single_column: true,
	});
	const $body = $(page.body);
	page.set_secondary_action(__("Refresh"), () => load(), "refresh");

	function load() {
		frappe.call("frappe_tools.frappe_tools.doctype.ai_bot_page.ai_bot_page.get_my_ai_pages")
			.then((r) => render(r.message || []));
	}

	function render(pages) {
		if (!pages.length) {
			$body.html(`<div class="text-muted" style="padding:60px;text-align:center;">
				${__("No pages have been shared with you yet.")}</div>`);
			return;
		}
		$body.html(`<div class="ai-pages-list" style="display:flex;flex-direction:column;gap:8px;max-width:720px;">
			${pages.map((p) => {
				const tag = p.require_auth
					? `<span class="indicator-pill blue">${__("Restricted")}</span>`
					: `<span class="indicator-pill green">${__("Public")}</span>`;
				return `<a class="ai-page-card" href="/ai-page?name=${encodeURIComponent(p.route || p.name)}" target="_blank"
						style="display:flex;align-items:center;justify-content:space-between;gap:10px;
						padding:12px 14px;border:1px solid var(--border-color);border-radius:8px;
						text-decoration:none;color:inherit;background:var(--card-bg);">
					<div>
						<div style="font-weight:600;font-size:13.5px;">${frappe.utils.escape_html(p.title || p.name)}</div>
						<div class="text-muted" style="font-size:11.5px;">${frappe.utils.escape_html(p.owner || "")}</div>
					</div>
					${tag}
				</a>`;
			}).join("")}
		</div>`);
	}

	load();
};

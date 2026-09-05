(() => {
	const PARAM = "document_extraction";
	let activeRequest = "";

	function extractionName() {
		return new URLSearchParams(window.location.search).get(PARAM) || "";
	}

	function schedule(attempt = 0) {
		const name = extractionName();
		if (!name || activeRequest === name) return;
		window.setTimeout(() => {
			if (!window.cur_frm?.doc?.name && attempt < 20) return schedule(attempt + 1);
			applyEvidence(name);
		}, attempt ? 180 : 80);
	}

	async function applyEvidence(name) {
		if (!window.cur_frm?.doc?.name) return;
		activeRequest = name;
		try {
			const response = await frappe.call({
				method: "frappe_tools.api.ocr_agent.get_run",
				args: { extraction: name },
			});
			const run = response.message;
			if (!run || run.target_doctype !== cur_frm.doctype || run.created_document !== cur_frm.doc.name) return;
			renderSummary(cur_frm, run);
			markFields(cur_frm, run);
		} catch (error) {
			console.warn("Could not load OCR evidence", error);
		}
	}

	function renderSummary(frm, run) {
		ensureStyles();
		frm.$wrapper.find(".ocr-desk-evidence").remove();
		const verified = [...(run.fields || []), ...(run.tables || []).flatMap((table) => table.rows || [])]
			.filter((item) => ["ocr", "manual"].includes(item.bbox?.source)).length;
		const corrected = (run.fields || []).filter((item) => ["Edited", "Approved"].includes(item.status)).length;
		const fieldRows = (run.fields || []).filter((item) => item.value !== "").slice(0, 8).map((item) => {
			const source = item.bbox?.source === "manual" ? "Human verified" : item.bbox?.source === "ocr" ? "OCR verified" : "Needs verification";
			return `<span><b>${escape(item.label)}</b><small>${escape(source)} · ${Math.round(Number(item.confidence || 0) * 100)}%</small></span>`;
		}).join("");
		const panel = $(`
			<section class="ocr-desk-evidence">
				<div class="ocr-evidence-head">
					<div><strong>Created from document capture</strong><small>${escape(run.name)} · ${run.pages.length} page${run.pages.length === 1 ? "" : "s"} · ${verified} verified source regions</small></div>
					<div class="ocr-evidence-actions"><span>${corrected} human-confirmed</span><button type="button" class="btn btn-xs btn-primary ocr-open-source">Open source & mapping</button><button type="button" class="btn btn-xs btn-default ocr-toggle-marks">Hide markings</button></div>
				</div>
				<div class="ocr-evidence-legend"><span class="ai">AI extracted</span><span class="human">Human confirmed</span><small>Click a marked field label to open its exact source evidence.</small></div>
				<div class="ocr-evidence-fields">${fieldRows}</div>
			</section>
		`);
		const layout = frm.$wrapper.find(".form-layout").first();
		(layout.length ? layout : frm.$wrapper).prepend(panel);
		panel.find(".ocr-open-source").on("click", () => window.open(`/ocr#${new URLSearchParams({ run: run.name })}`, "_blank", "noopener"));
		panel.find(".ocr-toggle-marks").on("click", function () {
			const hidden = frm.$wrapper.toggleClass("ocr-marks-hidden").hasClass("ocr-marks-hidden");
			$(this).text(hidden ? "Show markings" : "Hide markings");
		});
	}

	function markFields(frm, run) {
		for (const item of run.fields || []) {
			if (item.value === "" || !frm.fields_dict[item.fieldname]) continue;
			markWrapper(frm.fields_dict[item.fieldname].$wrapper, item, run);
		}
		for (const table of run.tables || []) {
			const wrapper = frm.fields_dict[table.fieldname]?.$wrapper;
			if (!wrapper || !table.rows?.length) continue;
			markWrapper(wrapper, {
				label: table.label,
				confidence: Math.min(...table.rows.map((row) => Number(row.confidence || 0))),
				bbox: table.rows.every((row) => ["ocr", "manual"].includes(row.bbox?.source)) ? { source: "ocr" } : { source: "model" },
			}, run);
		}
	}

	function markWrapper(wrapper, item, run) {
		const human = item.bbox?.source === "manual" || ["Edited", "Approved"].includes(item.status);
		wrapper.addClass(human ? "ocr-field-human" : "ocr-field-ai");
		const label = wrapper.find(".control-label").first();
		if (!label.length || label.find(".ocr-origin-badge").length) return;
		const score = Math.round(Number(item.confidence || 0) * 100);
		const badge = $(`<button type="button" class="ocr-origin-badge ${human ? "human" : "ai"}">${human ? "Human confirmed" : `AI ${score}%`}</button>`);
		badge.attr("title", `${item.raw_text || item.printed_value || "Source evidence"}`);
		badge.on("click", (event) => {
			event.preventDefault();
			event.stopPropagation();
			window.open(`/ocr#${new URLSearchParams({ run: run.name })}`, "_blank", "noopener");
		});
		label.append(badge);
	}

	function escape(value) {
		return frappe.utils.escape_html(String(value ?? ""));
	}

	function ensureStyles() {
		if (document.getElementById("ocr-desk-evidence-style")) return;
		const style = document.createElement("style");
		style.id = "ocr-desk-evidence-style";
		style.textContent = `
			.ocr-desk-evidence{margin:0 0 14px;border:1px solid #cfe1f2;border-radius:7px;background:#f5faff;overflow:hidden}
			.ocr-evidence-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 13px;border-bottom:1px solid #dce9f5}.ocr-evidence-head strong,.ocr-evidence-head small{display:block}.ocr-evidence-head strong{font-size:12px}.ocr-evidence-head small{margin-top:3px;color:#6f7d8f;font-size:10px}.ocr-evidence-actions{display:flex;align-items:center;gap:6px}.ocr-evidence-actions>span{padding:4px 7px;border-radius:10px;color:#087450;font-size:9px;background:#dff5ea}
			.ocr-evidence-legend{display:flex;align-items:center;gap:7px;padding:8px 13px}.ocr-evidence-legend span,.ocr-origin-badge{padding:3px 6px;border:0;border-radius:9px;font-size:9px;font-weight:600}.ocr-evidence-legend .ai,.ocr-origin-badge.ai{color:#176ca8;background:#deeffe}.ocr-evidence-legend .human,.ocr-origin-badge.human{color:#087450;background:#ddf4e8}.ocr-evidence-legend small{margin-left:auto;color:#748194;font-size:9px}
			.ocr-evidence-fields{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;border-top:1px solid #dce9f5;background:#dce9f5}.ocr-evidence-fields>span{padding:8px 10px;background:#fff}.ocr-evidence-fields b,.ocr-evidence-fields small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ocr-evidence-fields b{font-size:10px}.ocr-evidence-fields small{margin-top:3px;color:#718095;font-size:9px}
			.ocr-field-ai>.form-group,.ocr-field-ai .grid-heading-row{border-radius:5px;background:#f4faff;box-shadow:inset 3px 0 #2490ef}.ocr-field-human>.form-group,.ocr-field-human .grid-heading-row{border-radius:5px;background:#f2fbf7;box-shadow:inset 3px 0 #16a274}.ocr-origin-badge{margin-left:6px;vertical-align:middle;cursor:pointer}.ocr-marks-hidden .ocr-field-ai>.form-group,.ocr-marks-hidden .ocr-field-ai .grid-heading-row,.ocr-marks-hidden .ocr-field-human>.form-group,.ocr-marks-hidden .ocr-field-human .grid-heading-row{background:initial;box-shadow:none}.ocr-marks-hidden .ocr-origin-badge{display:none}
			@media(max-width:900px){.ocr-evidence-head{align-items:flex-start;flex-direction:column}.ocr-evidence-actions{flex-wrap:wrap}.ocr-evidence-fields{grid-template-columns:repeat(2,minmax(0,1fr))}}
		`;
		document.head.appendChild(style);
	}

	if (frappe.router?.on) frappe.router.on("change", () => { activeRequest = ""; schedule(); });
	$(document).on("form-refresh", () => { activeRequest = ""; schedule(); });
	if (typeof frappe.ready === "function") frappe.ready(schedule);
	else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", schedule, { once: true });
	else schedule();
})();

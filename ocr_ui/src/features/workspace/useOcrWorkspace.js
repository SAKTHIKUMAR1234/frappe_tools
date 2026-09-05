import { computed, reactive } from "vue";
import { call, uploadPage, errorMessage } from "@/services/api";
import { materializePage } from "@/services/images";

const METHOD = "frappe_tools.api.ocr_agent";
const GROUND_METHOD = "frappe_tools.api.ocr_grounding";
const RESOLVE_METHOD = "frappe_tools.api.doc_resolve";
const state = reactive({
  boot: null,
  view: "queue",
  queue: {
    items: [],
    counts: {},
    status: "all",
    loading: false,
    start: 0,
    page_length: 25,
    filters: {},
  },
  run: null,
  loading: false,
  starting: false,
  newTarget: "",
  saving: "",
  refining: false,
  upload: { completed: 0, total: 0, label: "" },
  error: "",
});

let pollTimer = null;
let pollVersion = 0;
let queueRequestVersion = 0;

export function useOcrWorkspace() {
  const active = computed(() => state.run);
  const isProcessing = computed(() => runIsProcessing(state.run));

  async function loadBoot() {
    state.loading = true;
    state.error = "";
    try {
      state.boot = await call(`${METHOD}.get_boot`);
      state.queue = { ...state.boot.queue, loading: false };
      const fromHash = new URLSearchParams(location.hash.replace(/^#/, "")).get(
        "run",
      );
      if (fromHash) await openRun(fromHash);
      else {
        state.view = "queue";
        startQueuePolling();
      }
    } catch (error) {
      state.error = errorMessage(error, "Could not open the OCR workspace.");
    } finally {
      state.loading = false;
    }
  }

  async function loadQueue(
    status = state.queue.status || "all",
    silent = false,
    query = {},
  ) {
    const requestVersion = ++queueRequestVersion;
    if (!silent) state.queue.loading = true;
    try {
      const existing = state.queue.filters || {};
      const start = query.start ?? state.queue.start ?? 0;
      const queue = await call(`${METHOD}.get_queue`, {
        status,
        start,
        page_length: state.queue.page_length || 25,
        search: query.search ?? existing.search ?? "",
        target_doctype: query.target_doctype ?? existing.target_doctype ?? "",
        created: query.created ?? existing.created ?? "all",
        date_from: query.date_from ?? existing.date_from ?? "",
        date_to: query.date_to ?? existing.date_to ?? "",
        sort_by: query.sort_by ?? existing.sort_by ?? "modified",
        sort_order: query.sort_order ?? existing.sort_order ?? "desc",
      });
      if (requestVersion !== queueRequestVersion) return;
      state.queue = { ...queue, loading: false };
      if (state.boot) state.boot.queue = queue;
    } catch (error) {
      if (requestVersion !== queueRequestVersion) return;
      state.queue.loading = false;
      if (!silent)
        state.error = errorMessage(
          error,
          "Could not refresh the upload queue.",
        );
    }
  }

  async function showQueue(status = "all", preservePosition = false) {
    stopPolling();
    state.view = "queue";
    state.run = null;
    state.error = "";
    history.replaceState(null, "", `${location.pathname}${location.search}`);
    await loadQueue(status, false, {
      start: preservePosition ? state.queue.start : 0,
    });
    startQueuePolling();
  }

  async function startRun(targetDoctype, selectedLayout, pages) {
    return startBatches([{ targetDoctype, selectedLayout, pages }]);
  }

  async function startBatches(batches, onCommitted = null) {
    const work = (batches || []).filter((batch) => batch?.pages?.length);
    if (!work.length) throw new Error("Add at least one document page.");
    state.starting = true;
    state.error = "";
    state.upload = {
      completed: 0,
      total: work.reduce((total, batch) => total + batch.pages.length, 0),
      label: "Preparing documents",
    };
    try {
      const extractionNames = [];
      let completed = 0;
      for (let batchIndex = 0; batchIndex < work.length; batchIndex += 1) {
        const batch = work[batchIndex];
        state.upload.label = `Creating document ${batchIndex + 1} of ${work.length}`;
        const session = await call(`${METHOD}.create_session`, {
          target_doctype: batch.targetDoctype || "",
          selected_layout: batch.selectedLayout || "",
          operation_mode: batch.operationMode || "Create New",
          existing_document: batch.existingDocument || "",
        });
        const fileUrls = [];
        for (let index = 0; index < batch.pages.length; index += 1) {
          state.upload.label = `Uploading document ${batchIndex + 1}, page ${index + 1} of ${batch.pages.length}`;
          const file = await materializePage(batch.pages[index]);
          const uploaded = await uploadPage(file, session.extraction);
          fileUrls.push(uploaded.file_url);
          completed += 1;
          state.upload.completed = completed;
        }
        await call(`${METHOD}.start_session`, {
          extraction: session.extraction,
          file_urls: fileUrls,
        });
        extractionNames.push(session.extraction);
        if (batch.phoneFolderId) {
          await onCommitted?.(batch.phoneFolderId, session.extraction);
        }
      }
      await openRun(extractionNames.at(-1));
      await refreshBoot();
      return extractionNames;
    } catch (error) {
      state.error = errorMessage(error, "Could not start document extraction.");
      throw error;
    } finally {
      state.starting = false;
    }
  }

  async function openRun(name) {
    stopPolling();
    state.loading = true;
    state.error = "";
    try {
      state.run = await call(`${METHOD}.get_run`, { extraction: name });
      state.view = "run";
      location.hash = new URLSearchParams({ run: name }).toString();
      if (runIsProcessing(state.run)) startRunPolling();
      else if (needsGrounding(state.run)) await refineGrounding();
    } catch (error) {
      state.error = errorMessage(error, "Could not load this extraction.");
    } finally {
      state.loading = false;
    }
  }

  async function refreshRun() {
    if (!state.run?.name) return;
    try {
      state.run = await call(`${METHOD}.get_run`, {
        extraction: state.run.name,
      });
      if (!runIsProcessing(state.run)) {
        stopPolling();
        await refreshBoot();
        if (needsGrounding(state.run)) await refineGrounding();
      }
    } catch (error) {
      state.error = errorMessage(error, "Could not refresh extraction status.");
    }
  }

  async function retryExtraction(item) {
    state.saving = `retry:${item.name}`;
    try {
      await call(`${METHOD}.retry_session`, { extraction: item.name });
      await openRun(item.name);
      await refreshBoot();
    } finally {
      state.saving = "";
    }
  }

  async function confirmClassification(targetDoctype) {
    state.saving = "classification";
    try {
      await call(`${METHOD}.confirm_classification`, {
        extraction: state.run.name,
        target_doctype: targetDoctype,
      });
      await refreshRun();
      startRunPolling();
    } finally {
      state.saving = "";
    }
  }

  async function confirmLayout(layout) {
    state.saving = "layout";
    try {
      await call(`${METHOD}.confirm_layout`, {
        extraction: state.run.name,
        layout,
      });
      await refreshRun();
      startRunPolling();
    } finally {
      state.saving = "";
    }
  }

  async function saveField(field, value, rejected = false) {
    state.saving = `field:${field.fieldname}`;
    try {
      state.run = await call(`${METHOD}.update_field`, {
        extraction: state.run.name,
        fieldname: field.fieldname,
        value,
        rejected: rejected ? 1 : 0,
      });
    } finally {
      state.saving = "";
    }
  }

  async function saveTableRow(table, row, values) {
    state.saving = `table:${table.fieldname}:${row.row_no}`;
    try {
      state.run = await call(`${METHOD}.update_table_row`, {
        extraction: state.run.name,
        table: table.fieldname,
        row_no: row.row_no,
        values,
      });
    } finally {
      state.saving = "";
    }
  }

  async function resolveTableRow(table, row, value) {
    state.saving = `resolve:${table.fieldname}:${row.row_no}`;
    try {
      await call(`${RESOLVE_METHOD}.confirm_line_item`, {
        extraction: state.run.name,
        row_no: row.row_no,
        item_code: value,
      });
      await refreshRun();
    } finally {
      state.saving = "";
    }
  }

  async function refineGrounding() {
    if (!state.run?.name || state.refining) return;
    state.refining = true;
    try {
      state.run = await call(`${GROUND_METHOD}.refresh_grounding`, {
        extraction: state.run.name,
      });
    } finally {
      state.refining = false;
    }
  }

  async function saveBBox(source, page, bbox) {
    state.saving = "bbox";
    try {
      const isTable = Boolean(source?.table && source?.row_no);
      state.run = await call(`${GROUND_METHOD}.update_bbox`, {
        extraction: state.run.name,
        scope: isTable ? "table" : "field",
        fieldname: isTable ? null : source?.fieldname,
        table: isTable ? source.table : null,
        row_no: isTable ? source.row_no : null,
        column: isTable ? source.column || null : null,
        page,
        bbox,
      });
    } finally {
      state.saving = "";
    }
  }

  async function createDraft() {
    state.saving = "create";
    try {
      const result = await call(`${METHOD}.create_draft`, {
        extraction: state.run.name,
      });
      await refreshRun();
      return result;
    } finally {
      state.saving = "";
    }
  }

  async function prepareDecision() {
    state.saving = "prepare-decision";
    try {
      await call(`${METHOD}.prepare_decision`, { extraction: state.run.name });
      await refreshRun();
    } finally {
      state.saving = "";
    }
  }

  async function runDecision() {
    state.saving = "run-decision";
    try {
      const result = await call(`${METHOD}.run_decision`, {
        extraction: state.run.name,
      });
      await refreshRun();
      if (runIsProcessing(state.run)) startRunPolling();
      return result;
    } finally {
      state.saving = "";
    }
  }

  async function applyDecision() {
    state.saving = "apply-decision";
    try {
      const result = await call(`${METHOD}.apply_decision`, {
        extraction: state.run.name,
      });
      await refreshRun();
      return result;
    } finally {
      state.saving = "";
    }
  }

  async function reprocessDecision() {
    state.saving = "reprocess-decision";
    try {
      const result = await call(`${METHOD}.reprocess_decision`, {
        extraction: state.run.name,
      });
      await refreshRun();
      if (runIsProcessing(state.run)) startRunPolling();
      return result;
    } finally {
      state.saving = "";
    }
  }

  async function searchLink(doctype, text) {
    return call(`${METHOD}.search_link`, { doctype, text, limit: 10 });
  }

  function newRun(targetDoctype = "") {
    state.newTarget = typeof targetDoctype === "string" ? targetDoctype : "";
    stopPolling();
    state.run = null;
    state.view = "new";
    state.error = "";
    history.replaceState(null, "", `${location.pathname}${location.search}`);
  }

  async function refreshBoot() {
    const boot = await call(`${METHOD}.get_boot`);
    state.boot = boot;
    if (state.view === "queue") await loadQueue(state.queue.status, true);
  }

  function startRunPolling() {
    schedulePolling("run");
  }

  function startQueuePolling() {
    schedulePolling("queue");
  }

  function schedulePolling(view) {
    stopPolling();
    const version = pollVersion;
    const schedule = () => {
      if (version !== pollVersion || state.view !== view) return;
      const delay = document.hidden
        ? 30000
        : view === "run"
          ? 2200
          : state.queue.counts?.pending
            ? 5000
            : 30000;
      pollTimer = window.setTimeout(async () => {
        if (version !== pollVersion || state.view !== view) return;
        if (!document.hidden) {
          if (view === "run") await refreshRun();
          else await loadQueue(state.queue.status, true);
        }
        schedule();
      }, delay);
    };
    schedule();
  }

  function stopPolling() {
    pollVersion += 1;
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = null;
  }

  return {
    state,
    active,
    isProcessing,
    loadBoot,
    loadQueue,
    showQueue,
    startRun,
    startBatches,
    openRun,
    refreshRun,
    retryExtraction,
    confirmClassification,
    confirmLayout,
    saveField,
    saveTableRow,
    resolveTableRow,
    saveBBox,
    refineGrounding,
    createDraft,
    prepareDecision,
    runDecision,
    applyDecision,
    reprocessDecision,
    searchLink,
    newRun,
    stopPolling,
  };
}

function runIsProcessing(run) {
  if (!run) return false;
  if (["Preparing", "Classifying", "Queued", "Extracting"].includes(run.status))
    return true;
  return (
    run.status === "Review" &&
    ["Queued", "Running"].includes(run.decision?.phase)
  );
}

function needsGrounding(run) {
  const evidence = [
    ...(run?.fields || []),
    ...(run?.tables || []).flatMap((table) => table.rows || []),
  ];
  return evidence.some(
    (item) =>
      Number(item.bbox?.w) > 0 && Number(item.bbox?.h) > 0 && !item.bbox.source,
  );
}

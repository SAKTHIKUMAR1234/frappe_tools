<script setup>
import { defineAsyncComponent, onMounted, onUnmounted, ref } from "vue";
import { useToast } from "primevue/usetoast";
import Button from "primevue/button";
import Dialog from "primevue/dialog";
import Message from "primevue/message";
import ProgressSpinner from "primevue/progressspinner";
import AppShell from "@/components/AppShell.vue";
import QueueView from "./QueueView.vue";
const AgentProgress = defineAsyncComponent(() => import("./AgentProgress.vue"));
const NewDocumentPanel = defineAsyncComponent(
  () => import("./NewDocumentPanel.vue"),
);
const ReviewWorkspace = defineAsyncComponent(
  () => import("@/adapters/AdapterReview.vue"),
);
import { useOcrWorkspace } from "./useOcrWorkspace";
import { errorMessage } from "@/services/api";

const toast = useToast();
const {
  state,
  isProcessing,
  loadBoot,
  loadQueue,
  showQueue,
  startRun,
  startBatches,
  openRun,
  retryExtraction,
  confirmClassification,
  confirmLayout,
  saveField,
  saveTableRow,
  resolveTableRow,
  saveBBox,
  createDraft,
  prepareDecision,
  runDecision,
  applyDecision,
  reprocessDecision,
  newRun,
  stopPolling,
} = useOcrWorkspace();

const dirty = ref(false);
const leaveDialog = ref(false);
let pendingNavigation = null;

function navigate(action) {
  if (state.starting || state.saving) {
    toast.add({
      severity: "info",
      summary: "Please wait",
      detail: "Let the current save finish before leaving.",
      life: 3000,
    });
    return;
  }
  if (dirty.value) {
    pendingNavigation = action;
    leaveDialog.value = true;
  } else action();
}
function discardAndLeave() {
  const action = pendingNavigation;
  pendingNavigation = null;
  dirty.value = false;
  leaveDialog.value = false;
  action?.();
}
function warnBeforeUnload(event) {
  if (!dirty.value && !state.starting && !state.saving) return;
  event.preventDefault();
  event.returnValue = "";
}
onMounted(() => {
  loadBoot();
  window.addEventListener("beforeunload", warnBeforeUnload);
});
onUnmounted(() => {
  stopPolling();
  window.removeEventListener("beforeunload", warnBeforeUnload);
});

async function start(payload) {
  try {
    if (payload.batches) {
      await startBatches(payload.batches, payload.onCommitted);
    } else {
      await startRun(
        payload.targetDoctype,
        payload.selectedLayout,
        payload.pages,
      );
    }
    toast.add({
      severity: "info",
      summary: "Added to background queue",
      detail: "Your document is ready to be processed.",
      life: 3500,
    });
  } catch (error) {
    toast.add({
      severity: "error",
      summary: "Could not start",
      detail: errorMessage(error),
      life: 6000,
    });
  }
}

async function retry(item) {
  try {
    await retryExtraction(item);
    toast.add({
      severity: "info",
      summary: "Requeued",
      detail: item.name,
      life: 2500,
    });
  } catch (error) {
    toast.add({
      severity: "error",
      summary: "Could not retry",
      detail: errorMessage(error),
      life: 5000,
    });
  }
}

async function classify(targetDoctype) {
  try {
    await confirmClassification(targetDoctype);
    toast.add({
      severity: "success",
      summary: "Document type confirmed",
      detail: `${targetDoctype} extraction is now queued.`,
      life: 3000,
    });
  } catch (error) {
    toast.add({
      severity: "error",
      summary: "Could not continue",
      detail: errorMessage(error),
      life: 5000,
    });
  }
}

async function chooseLayout(layout) {
  try {
    await confirmLayout(layout);
    toast.add({
      severity: "success",
      summary: "Document layout confirmed",
      detail: `${layout} page ordering is now queued.`,
      life: 3000,
    });
  } catch (error) {
    toast.add({
      severity: "error",
      summary: "Could not continue",
      detail: errorMessage(error),
      life: 5000,
    });
  }
}

async function updateField(field, value) {
  const unchanged = String(field.value ?? "") === String(value ?? "");
  if (unchanged && ["Approved", "Edited"].includes(field.status)) return;
  try {
    await saveField(field, value);
    toast.add({
      severity: "success",
      summary: "Saved",
      detail: field.label,
      life: 1200,
    });
  } catch (error) {
    toast.add({
      severity: "error",
      summary: "Could not save field",
      detail: errorMessage(error),
      life: 5000,
    });
  }
}

async function updateRow(table, row, values) {
  try {
    await saveTableRow(table, row, values);
    toast.add({
      severity: "success",
      summary: "Row saved",
      detail: `${table.label} · ${row.row_no}`,
      life: 1200,
    });
  } catch (error) {
    toast.add({
      severity: "error",
      summary: "Could not save row",
      detail: errorMessage(error),
      life: 5000,
    });
  }
}

async function resolveRow(table, row, value) {
  try {
    await resolveTableRow(table, row, value);
    toast.add({
      severity: "success",
      summary: "Reference confirmed",
      detail: `${table.resolver?.label || "Mapped record"}: ${value}`,
      life: 2500,
    });
  } catch (error) {
    toast.add({
      severity: "error",
      summary: "Reference was not confirmed",
      detail: errorMessage(error),
      life: 5000,
    });
  }
}

async function correctEvidence(source, page, bbox) {
  try {
    await saveBBox(source, page, bbox);
    toast.add({
      severity: "success",
      summary: "Evidence verified",
      detail: `Page ${page}`,
      life: 1800,
    });
  } catch (error) {
    toast.add({
      severity: "error",
      summary: "Could not save highlight",
      detail: errorMessage(error),
      life: 5000,
    });
  }
}

async function finish() {
  try {
    const result = await createDraft();
    toast.add({
      severity: "success",
      summary: "Draft created",
      detail: `${result.doctype} ${result.docname}`,
      life: 5000,
    });
  } catch (error) {
    toast.add({
      severity: "error",
      summary: "Draft was not created",
      detail: errorMessage(error),
      life: 7000,
    });
  }
}

async function collectReferences() {
  try {
    await prepareDecision();
    toast.add({
      severity: "success",
      summary: "Local records checked",
      detail: "Local ERP evidence is ready.",
      life: 2200,
    });
  } catch (error) {
    toast.add({
      severity: "error",
      summary: "Could not check local records",
      detail: errorMessage(error),
      life: 6000,
    });
  }
}

async function decide() {
  try {
    const result = await runDecision();
    toast.add({
      severity: result.queued
        ? "info"
        : result.status === "handoff"
          ? "warn"
          : "success",
      summary: result.queued
        ? "Matching queued"
        : result.status === "handoff"
          ? "Human handoff required"
          : "Matches ready",
      detail: result.queued
        ? "Verification is running in the background. You can safely leave this page."
        : (result.handoff_reasons || result.reasons || []).join(", "),
      life: 5000,
    });
  } catch (error) {
    toast.add({
      severity: "error",
      summary: "Could not complete matching",
      detail: errorMessage(error),
      life: 6000,
    });
  }
}

async function applyAgentDecision() {
  try {
    await applyDecision();
    toast.add({
      severity: "success",
      summary: "Matched values applied",
      detail:
        "Existing-record selections were copied to staging. No target document was created.",
      life: 4000,
    });
  } catch (error) {
    toast.add({
      severity: "error",
      summary: "Matched values were not applied",
      detail: errorMessage(error),
      life: 6000,
    });
  }
}

async function reprocessAgentDecision() {
  try {
    await reprocessDecision();
    toast.add({
      severity: "info",
      summary: "Matching started again",
      detail: "Stored OCR was reused. Vision was not called again.",
      life: 3500,
    });
  } catch (error) {
    toast.add({
      severity: "error",
      summary: "Could not reprocess",
      detail: errorMessage(error),
      life: 6000,
    });
  }
}

function targetRoute(targetDoctype, docname, extraction) {
  const slug = targetDoctype.toLowerCase().replace(/\s+/g, "-");
  const query = new URLSearchParams({ document_extraction: extraction });
  return `/app/${slug}/${encodeURIComponent(docname)}?${query}`;
}

function openCreatedDocument() {
  if (!state.run?.created_document) return;
  window.open(
    targetRoute(
      state.run.target_doctype,
      state.run.created_document,
      state.run.name,
    ),
    "_blank",
    "noopener",
  );
}

function openCreatedItem(item) {
  if (!item?.created_document || !item.can_open_created) return;
  window.open(
    targetRoute(item.target_doctype, item.created_document, item.name),
    "_blank",
    "noopener",
  );
}
</script>

<template>
  <AppShell
    :boot="state.boot"
    :run="state.run"
    :view="state.view"
    :queue-status="state.queue.status"
    @new="navigate(() => newRun())"
    @queue="(status) => navigate(() => showQueue(status))"
    @open="(name) => navigate(() => openRun(name))"
  >
    <div v-if="state.error" class="global-message">
      <Message severity="error" :closable="false">{{ state.error }}</Message>
    </div>

    <div v-if="state.loading && !state.boot" class="loading-screen">
      <ProgressSpinner />
      <strong>Loading documents…</strong>
    </div>

    <QueueView
      v-else-if="state.view === 'queue'"
      :queue="state.queue"
      :doctypes="state.boot?.doctypes || []"
      :processing="state.boot?.processing"
      @new="newRun"
      @status="showQueue"
      @open="openRun"
      @retry="retry"
      @open-created="openCreatedItem"
      @refresh="loadQueue(state.queue.status)"
      @query="
        (query) => loadQueue(query.status || state.queue.status, false, query)
      "
    />

    <NewDocumentPanel
      v-else-if="state.view === 'new'"
      :initial-target="state.newTarget"
      :doctypes="state.boot?.doctypes || []"
      :layouts="state.boot?.layouts || []"
      :limits="state.boot?.limits"
      :processing="state.boot?.processing"
      :user="state.boot?.user || ''"
      :site-name="state.boot?.site_name || ''"
      :busy="state.starting"
      :upload="state.upload"
      @start="start"
      @cancel="navigate(() => showQueue('all'))"
      @dirty-change="dirty = $event"
    />

    <AgentProgress
      v-else-if="
        state.run &&
        (isProcessing ||
          [
            'Draft',
            'Classification Handoff',
            'Layout Handoff',
            'Failed',
          ].includes(state.run.status))
      "
      :run="state.run"
      :doctypes="state.boot?.doctypes || []"
      :processing="state.boot?.processing"
      :busy="['classification', 'layout'].includes(state.saving)"
      @retry="retry({ name: state.run.name })"
      @confirm-classification="classify"
      @confirm-layout="chooseLayout"
      @new="showQueue(state.run.status === 'Failed' ? 'error' : 'pending')"
    />

    <ReviewWorkspace
      v-else-if="state.run"
      :key="state.run.name"
      :run="state.run"
      :saving="state.saving"
      :processing="state.boot?.processing"
      :refining="state.refining"
      @save-field="updateField"
      @save-row="updateRow"
      @resolve-row="resolveRow"
      @correct-bbox="correctEvidence"
      @create="finish"
      @prepare-decision="collectReferences"
      @run-decision="decide"
      @apply-decision="applyAgentDecision"
      @reprocess-decision="reprocessAgentDecision"
      @new="navigate(() => showQueue(state.queue.status || 'all', true))"
      @dirty-change="dirty = $event"
    />

    <Button
      v-if="state.run?.status === 'Created'"
      class="open-created-fab"
      label="Open created draft in Desk"
      icon="pi pi-external-link"
      @click="openCreatedDocument"
    />
    <Dialog
      v-model:visible="leaveDialog"
      modal
      header="Leave without saving?"
      :style="{ width: '26rem', maxWidth: 'calc(100vw - 2rem)' }"
    >
      <p>
        Your unsaved edits or staged pages will be discarded. Saved document
        data is kept.
      </p>
      <template #footer>
        <Button
          label="Keep working"
          severity="secondary"
          autofocus
          @click="leaveDialog = false"
        />
        <Button
          label="Discard & leave"
          severity="danger"
          @click="discardAndLeave"
        />
      </template>
    </Dialog>
  </AppShell>
</template>

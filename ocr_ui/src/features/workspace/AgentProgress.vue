<script setup>
import { computed, ref } from "vue";
import Button from "primevue/button";
import Message from "primevue/message";
import ProgressBar from "primevue/progressbar";
import Select from "primevue/select";

const props = defineProps({
  run: { type: Object, required: true },
  doctypes: { type: Array, default: () => [] },
  busy: Boolean,
  processing: { type: Object, default: () => ({}) },
});
const emit = defineEmits([
  "retry",
  "new",
  "confirm-classification",
  "confirm-layout",
]);
const selectedTarget = ref(null);
const selectedLayout = ref(null);
const classificationOptions = computed(() =>
  props.doctypes.map((item) => ({
    label: item.label || item.doctype,
    value: item.doctype,
  })),
);
const layoutOptions = computed(() =>
  (props.run.layout?.candidates || []).map((name) => ({
    label: name,
    value: name,
  })),
);

function confirmClassification() {
  if (selectedTarget.value)
    emit("confirm-classification", selectedTarget.value);
}

function confirmLayout() {
  if (selectedLayout.value) emit("confirm-layout", selectedLayout.value);
}

function stageIcon(stage) {
  if (stage.state === "done") return "pi pi-check";
  if (stage.state === "failed") return "pi pi-times";
  if (stage.state === "active") return "pi pi-spin pi-spinner";
  return "pi pi-circle";
}
</script>

<template>
  <section class="progress-view page-enter">
    <header class="workspace-header">
      <div>
        <h1>
          {{
            ["Classification Handoff", "Layout Handoff"].includes(run.status)
              ? run.status === "Layout Handoff"
                ? "Confirm document layout"
                : "Confirm document type"
              : run.status === "Failed"
                ? "Extraction failed"
                : ["Preparing", "Classifying", "Queued"].includes(run.status)
                  ? `Queued ${run.target_label}`
                  : `Processing ${run.target_label}`
          }}
        </h1>
        <span>{{ run.name }}</span>
      </div>
      <span class="header-meta"><i class="pi pi-lock" /> Pages saved</span>
    </header>

    <div class="processing-workspace">
      <aside class="processing-preview">
        <div class="pane-heading">
          <span class="pane-icon"><i class="pi pi-file" /></span>
          <div>
            <strong>Document pages</strong
            ><small>{{ run.pages.length }} attached</small>
          </div>
        </div>
        <div class="processing-pages">
          <img
            v-for="page in run.pages"
            :key="page.page_no"
            :src="page.image"
            :alt="`Page ${page.page_no}`"
          />
        </div>
      </aside>

      <section class="processing-status">
        <div class="processing-title">
          <span class="agent-orb"
            ><i
              :class="
                run.status === 'Failed' ? 'pi pi-times' : 'pi pi-sparkles'
              "
          /></span>
          <div>
            <h2>
              {{
                ["Classification Handoff", "Layout Handoff"].includes(
                  run.status,
                )
                  ? run.status === "Layout Handoff"
                    ? "Choose the correct document layout to continue"
                    : "Choose the correct process to continue"
                  : run.status === "Failed"
                    ? "This document could not be processed"
                    : run.status === "Classifying"
                      ? "Identifying the document type"
                      : run.status === "Preparing"
                        ? "Preparing uploaded pages"
                        : run.status === "Queued"
                          ? "Waiting to start"
                          : "Reading and validating document"
              }}
            </h2>
            <p>
              {{
                ["Classification Handoff", "Layout Handoff"].includes(
                  run.status,
                )
                  ? run.status === "Layout Handoff"
                    ? "The system did not guess the package layout or page order. Confirm it once."
                    : "The system did not guess. Your pages are safe; confirm LR or Purchase Invoice once."
                  : run.status === "Failed"
                    ? "Your uploaded pages remain safely attached."
                    : run.status === "Queued"
                      ? `This ${run.pages.length}-page document is waiting to be processed.`
                      : "This page refreshes automatically when review is ready."
              }}
            </p>
          </div>
        </div>

        <div
          v-if="run.status === 'Layout Handoff'"
          class="classification-handoff"
        >
          <Message severity="warn" :closable="false">
            <strong>Document layout or page order needs confirmation.</strong>
            <span v-if="run.layout?.result?.reasons?.length">
              {{ run.layout.result.reasons.join(" · ") }}
            </span>
          </Message>
          <div class="classification-action">
            <Select
              v-model="selectedLayout"
              :options="layoutOptions"
              option-label="label"
              option-value="value"
              placeholder="Choose the document layout"
              fluid
            />
            <Button
              label="Confirm & order pages"
              icon="pi pi-arrow-right"
              icon-pos="right"
              :loading="busy"
              :disabled="!selectedLayout"
              @click="confirmLayout"
            />
          </div>
        </div>
        <div class="stage-card">
          <div
            v-for="stage in run.stages"
            :key="stage.key"
            class="stage-item"
            :class="stage.state"
          >
            <span class="stage-dot"><i :class="stageIcon(stage)" /></span>
            <span>{{ stage.label }}</span>
          </div>
          <ProgressBar
            v-if="
              ['Preparing', 'Classifying', 'Queued', 'Extracting'].includes(
                run.status,
              )
            "
            mode="indeterminate"
            style="height: 3px"
          />
        </div>

        <div
          v-if="run.status === 'Classification Handoff'"
          class="classification-handoff"
        >
          <Message severity="warn" :closable="false">
            <strong>Document type needs confirmation.</strong>
            <span v-if="run.classification?.result?.reasons?.length">
              {{ run.classification.result.reasons.join(" · ") }}
            </span>
          </Message>
          <div class="classification-action">
            <Select
              v-model="selectedTarget"
              :options="classificationOptions"
              option-label="label"
              option-value="value"
              placeholder="Choose LR or Purchase Invoice"
              fluid
            />
            <Button
              label="Confirm & continue"
              icon="pi pi-arrow-right"
              icon-pos="right"
              :loading="busy"
              :disabled="!selectedTarget"
              @click="confirmClassification"
            />
          </div>
        </div>

        <Message
          v-if="run.status === 'Failed'"
          severity="error"
          :closable="false"
        >
          {{ run.error || "This document could not be processed." }}
        </Message>
        <div v-if="run.status === 'Failed'" class="center-actions">
          <Button
            label="Retry background job"
            icon="pi pi-refresh"
            size="small"
            @click="$emit('retry')"
          />
          <Button
            label="Back to uploads"
            text
            size="small"
            @click="$emit('new')"
          />
        </div>
        <div
          v-else-if="
            !['Classification Handoff', 'Layout Handoff'].includes(run.status)
          "
          class="center-actions"
        >
          <Button
            label="Back to uploads"
            icon="pi pi-arrow-left"
            text
            size="small"
            @click="$emit('new')"
          />
        </div>
      </section>
    </div>
  </section>
</template>

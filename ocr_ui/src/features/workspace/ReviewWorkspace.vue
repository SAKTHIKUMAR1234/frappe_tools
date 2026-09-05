<script setup>
import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  reactive,
  ref,
  watch,
} from "vue";
import Button from "primevue/button";
import Message from "primevue/message";
import Tag from "primevue/tag";
import DocumentViewer from "./DocumentViewer.vue";
import FieldReview from "./FieldReview.vue";
import TableReview from "./TableReview.vue";
import {
  reviewSections,
  fieldEvidence,
  reviewIssueTarget,
} from "@/services/workflow";
import { hasUnsavedDrafts } from "@/services/reviewDrafts";

const props = defineProps({
  run: { type: Object, required: true },
  saving: { type: String, default: "" },
  refining: Boolean,
  processing: { type: Object, default: () => ({}) },
  adapter: { type: Object, default: () => ({}) },
  context: { type: Object, default: () => ({}) },
});
const emit = defineEmits([
  "save-field",
  "save-row",
  "resolve-row",
  "correct-bbox",
  "create",
  "prepare-decision",
  "run-decision",
  "apply-decision",
  "reprocess-decision",
  "new",
  "dirty-change",
]);
const evidence = ref(null);
const activeTab = ref("");
const mobilePane = ref("review");
const reviewPanel = ref(null);
const drafts = reactive({});
const sections = computed(() => reviewSections(props.run));
const hasUnsaved = computed(() => hasUnsavedDrafts(drafts));
watch(hasUnsaved, (value) => emit("dirty-change", value));
onUnmounted(() => emit("dirty-change", false));

watch(
  sections,
  (value) => {
    if (
      ![...value.map((section) => section.key), "checks", "matching"].includes(
        activeTab.value,
      )
    ) {
      activeTab.value = value[0]?.key || "checks";
    }
  },
  { immediate: true },
);

const errorIssues = computed(() =>
  props.run.issues.filter((issue) => issue.severity === "error"),
);
const warningIssues = computed(() =>
  props.run.issues.filter((issue) => issue.severity === "warning"),
);
const action = computed(() => props.run.action_preview || {});
const phases = computed(() => props.run.review_phases?.steps || []);
const currentPhase = computed(() =>
  phases.value.find((step) => step.status === "active"),
);
const phaseError = ref("");
async function advancePhase(operation) {
  phaseError.value = "";
  try {
    const result = await props.context.phase(currentPhase.value.key, operation);
    if (operation === "confirm") {
      activeTab.value =
        result.steps.find((step) => step.status === "active")?.sections[0] ||
        "checks";
      mobilePane.value = "review";
    }
  } catch (error) {
    phaseError.value = error.message || "Could not complete this phase.";
  }
}
watch(
  () => props.run.name,
  () => {
    if (currentPhase.value) activeTab.value = currentPhase.value.sections[0];
  },
  { immediate: true },
);

function hasVisibleEvidence(source) {
  return (
    Number(source?.source_page) > 0 &&
    Number(source?.bbox?.w) > 0 &&
    Number(source?.bbox?.h) > 0
  );
}

function firstVisibleEvidence(run) {
  return (
    sections.value
      .flatMap((section) => section.fields)
      .find(hasVisibleEvidence) ||
    (run?.tables || [])
      .flatMap((table) => table.rows || [])
      .find(hasVisibleEvidence) ||
    null
  );
}

watch(
  () => props.run,
  (run) => {
    const selected = evidence.value;
    if (!selected) {
      evidence.value = firstVisibleEvidence(run);
      return;
    }
    const refreshed = fieldEvidence(run, selected);
    evidence.value = hasVisibleEvidence(refreshed)
      ? refreshed
      : firstVisibleEvidence(run);
  },
  { immediate: true },
);

function focus(source) {
  evidence.value = source;
  mobilePane.value = "document";
}

async function focusIssue(issue) {
  evidence.value = { ...issue, ...(fieldEvidence(props.run, issue) || {}) };
  const target = reviewIssueTarget(sections.value, issue);
  if (!target) return;
  mobilePane.value = "review";
  activeTab.value = target.section;
  await nextTick();
  const input = target.inputId && document.getElementById(target.inputId);
  if (input && reviewPanel.value?.contains(input)) {
    input.scrollIntoView({ block: "center", behavior: "instant" });
    input.focus({ preventScroll: true });
  } else {
    reviewPanel.value?.querySelector(".review-scroll")?.scrollTo({ top: 0 });
  }
}

function openChecks() {
  activeTab.value = "checks";
  mobilePane.value = "review";
}

const tabItems = computed(() => [
  ...sections.value.map((section) => ({
    key: section.key,
    label: section.label,
    count: section.fields.length + section.tables.length,
  })),
  { key: "checks", label: "Checks", count: props.run.issues.length },
  ...(!phases.value.length
    ? [{ key: "matching", label: "Verification", count: null }]
    : []),
]);
const activeIndex = computed(() =>
  tabItems.value.findIndex((item) => item.key === activeTab.value),
);
const verticalOutline = ref(window.innerWidth >= 1200);
const resizeOutline = () => {
  verticalOutline.value = window.innerWidth >= 1200;
};
onMounted(() =>
  window.addEventListener("resize", resizeOutline, { passive: true }),
);
onUnmounted(() => window.removeEventListener("resize", resizeOutline));
function tabKey(event, index) {
  let target = index;
  if (["ArrowDown", "ArrowRight"].includes(event.key))
    target = (index + 1) % tabItems.value.length;
  else if (["ArrowUp", "ArrowLeft"].includes(event.key))
    target = (index - 1 + tabItems.value.length) % tabItems.value.length;
  else if (event.key === "Home") target = 0;
  else if (event.key === "End") target = tabItems.value.length - 1;
  else return;
  event.preventDefault();
  activeTab.value = tabItems.value[target].key;
  nextTick(() =>
    document.getElementById("review-tab-" + activeTab.value)?.focus(),
  );
}
function nextSection() {
  activeTab.value =
    tabItems.value[
      Math.min(activeIndex.value + 1, tabItems.value.length - 1)
    ].key;
}
</script>

<template>
  <section class="review-desk">
    <header class="review-topline">
      <button class="back-link" @click="$emit('new')">
        <i class="pi pi-arrow-left" /> Documents
      </button>
      <div class="review-document-title">
        <h1>{{ run.target_label }}</h1>
        <span
          >{{ run.name
          }}<template v-if="run.layout?.selected">
            · {{ run.layout.selected }}</template
          ></span
        >
      </div>
      <div class="review-state">
        <span
          class="status-pill"
          :class="errorIssues.length ? 'ready' : 'validated'"
          ><span />{{
            errorIssues.length
              ? errorIssues.length + " checks to resolve"
              : "Ready to confirm"
          }}</span
        ><small
          >{{ run.pages.length }} source page{{
            run.pages.length === 1 ? "" : "s"
          }}</small
        >
      </div>
    </header>
    <Message
      v-if="!phases.length && run.decision?.phase === 'Stale'"
      severity="warn"
      :closable="false"
    >
      Saved changes need verification again. The previous decision is kept for
      audit and cannot create a document.
      <Button
        label="Open verification"
        size="small"
        text
        @click="
          activeTab = 'matching';
          mobilePane = 'review';
        "
      />
    </Message>
    <section
      v-if="phases.length"
      class="phase-progress"
      aria-label="Review phases"
    >
      <nav aria-label="Phase progress">
        <button
          v-for="(step, index) in phases"
          :key="step.key"
          :class="{
            complete: step.status === 'complete',
            current: step.status === 'active',
          }"
          :aria-current="step.status === 'active' ? 'step' : undefined"
          @click="
            activeTab = step.sections[0];
            mobilePane = 'review';
          "
        >
          <span>{{ step.status === "complete" ? "✓" : index + 1 }}</span>
          {{ step.label }}
        </button>
      </nav>
      <div v-if="currentPhase">
        <p><strong>Automatic:</strong> {{ currentPhase.automation }}</p>
        <p><strong>Your review:</strong> {{ currentPhase.human_input }}</p>
        <Button
          v-if="currentPhase.can_automate"
          label="Refresh this phase's suggestions"
          icon="pi pi-refresh"
          size="small"
          outlined
          :disabled="hasUnsaved || !!saving"
          :loading="saving === 'phase:automate'"
          @click="advancePhase('automate')"
        />
      </div>
      <p v-else>
        All phases are confirmed. Review the checks and create the document.
      </p>
      <Message v-if="phaseError" severity="error" :closable="false">{{
        phaseError
      }}</Message>
    </section>
    <nav class="review-mobile-switch" aria-label="Review workspace">
      <button
        :class="{ active: mobilePane === 'review' }"
        :aria-pressed="mobilePane === 'review'"
        @click="mobilePane = 'review'"
      >
        <i class="pi pi-list-check" /> Review
        <span>{{ errorIssues.length || run.fields.length }}</span>
      </button>
      <button
        :class="{ active: mobilePane === 'document' }"
        :aria-pressed="mobilePane === 'document'"
        @click="mobilePane = 'document'"
      >
        <i class="pi pi-image" /> Document
      </button>
    </nav>
    <div class="review-board">
      <aside class="review-outline">
        <nav
          role="tablist"
          aria-label="Document sections"
          :aria-orientation="verticalOutline ? 'vertical' : 'horizontal'"
        >
          <button
            v-for="(item, index) in tabItems"
            :id="'review-tab-' + item.key"
            :key="item.key"
            role="tab"
            :aria-label="item.label"
            :aria-selected="activeTab === item.key"
            :aria-controls="'review-section-' + item.key"
            :tabindex="activeTab === item.key ? 0 : -1"
            :class="{ active: activeTab === item.key }"
            @click="
              activeTab = item.key;
              mobilePane = 'review';
            "
            @keydown="tabKey($event, index)"
          >
            <span class="section-number">{{
              String(index + 1).padStart(2, "0")
            }}</span
            ><span>{{ item.label }}</span
            ><small v-if="item.count !== null">{{ item.count }}</small>
          </button>
        </nav>
        <div class="review-outline-note">
          <i class="pi pi-shield" />
          <p>
            Check the highlighted facts. The source is always one click away.
          </p>
        </div>
      </aside>
      <section
        ref="reviewPanel"
        class="review-panel"
        :class="{ 'mobile-pane-active': mobilePane === 'review' }"
      >
        <div class="review-scroll">
          <template v-for="section in sections" :key="section.key">
            <section
              v-if="activeTab === section.key"
              :id="'review-section-' + section.key"
              role="tabpanel"
              :aria-labelledby="'review-tab-' + section.key"
            >
              <header class="review-section-heading">
                <h2>{{ section.title || section.label }}</h2>
                <p v-if="section.description">{{ section.description }}</p>
              </header>
              <component
                :is="adapter.sections?.[section.key]"
                v-if="adapter.sections?.[section.key]"
                :run="run"
                :section="section"
                :saving="saving"
                :drafts="drafts"
                :context="context"
                @focus="focus"
                @save-field="(field, value) => emit('save-field', field, value)"
                @save-row="
                  (table, row, values) => emit('save-row', table, row, values)
                "
                @resolve-row="
                  (table, row, value) => emit('resolve-row', table, row, value)
                "
              />
              <template v-else>
                <FieldReview
                  v-if="section.fields.length"
                  :fields="section.fields"
                  :issues="run.issues"
                  :saving="saving"
                  :drafts="drafts"
                  @focus="focus"
                  @save="(field, value) => emit('save-field', field, value)"
                />
                <TableReview
                  v-if="section.tables.length"
                  :tables="section.tables"
                  :issues="run.issues"
                  :saving="saving"
                  :drafts="drafts"
                  @focus="focus"
                  @save="
                    (table, row, values) => emit('save-row', table, row, values)
                  "
                  @resolve="
                    (table, row, value) =>
                      emit('resolve-row', table, row, value)
                  "
                />
              </template>
              <button class="next-section" @click="nextSection">
                Next:
                {{
                  tabItems[Math.min(activeIndex + 1, tabItems.length - 1)]
                    ?.label
                }}
                <i class="pi pi-arrow-right" />
              </button>
            </section>
          </template>
          <section
            v-if="activeTab === 'checks'"
            id="review-section-checks"
            role="tabpanel"
            aria-labelledby="review-tab-checks"
          >
            <header class="review-section-heading">
              <h2>Resolve the open checks</h2>
              <p>
                Select a check to go straight to the field that needs attention.
              </p>
            </header>
            <div v-if="run.issues.length" class="issue-list">
              <button
                v-for="issue in run.issues"
                :key="issue.path + issue.message"
                class="issue-card"
                :class="issue.severity"
                @click="focusIssue(issue)"
              >
                <i
                  :class="
                    issue.severity === 'error'
                      ? 'pi pi-exclamation-circle'
                      : 'pi pi-info-circle'
                  "
                /><span
                  ><strong>{{
                    issue.severity === "error" ? "Required check" : "Suggestion"
                  }}</strong
                  ><small>{{ issue.message }}</small></span
                ><i class="pi pi-arrow-right" />
              </button>
            </div>
            <Message v-else severity="success" :closable="false"
              >All required values and deterministic checks passed.</Message
            >
          </section>
          <section
            v-if="activeTab === 'matching'"
            id="review-section-matching"
            role="tabpanel"
            aria-labelledby="review-tab-matching"
          >
            <header class="review-section-heading">
              <h2>{{ run.workflow?.matching_label || "Reference checks" }}</h2>
              <p>
                Check the extracted details against existing records and resolve
                any uncertain matches.
              </p>
            </header>
            <div class="decision-panel">
              <Message v-if="hasUnsaved" severity="warn" :closable="false">
                Save your field and row changes before running verification.
              </Message>
              <div class="decision-actions">
                <Tag
                  :value="run.decision?.phase || 'Not prepared'"
                  :severity="
                    ['Human Handoff', 'Stale'].includes(run.decision?.phase)
                      ? 'warn'
                      : 'secondary'
                  "
                />
                <Button
                  label="Check local records"
                  icon="pi pi-database"
                  outlined
                  :disabled="hasUnsaved || Boolean(saving)"
                  :loading="saving === 'prepare-decision'"
                  @click="$emit('prepare-decision')"
                />
                <Button
                  label="Run verifier"
                  icon="pi pi-sparkles"
                  :disabled="hasUnsaved || Boolean(saving)"
                  :loading="saving === 'run-decision'"
                  @click="$emit('run-decision')"
                />
                <Button
                  v-if="
                    ['Human Handoff', 'Stale'].includes(run.decision?.phase)
                  "
                  label="Reprocess after fixing master data"
                  icon="pi pi-refresh"
                  outlined
                  :disabled="hasUnsaved || Boolean(saving)"
                  :loading="saving === 'reprocess-decision'"
                  @click="$emit('reprocess-decision')"
                />
                <Button
                  v-if="run.decision?.phase === 'Review Ready'"
                  label="Use matched values"
                  icon="pi pi-check-circle"
                  severity="success"
                  :disabled="hasUnsaved || Boolean(saving)"
                  :loading="saving === 'apply-decision'"
                  @click="$emit('apply-decision')"
                />
              </div>
              <Message
                v-if="run.decision?.handoff_reason"
                severity="warn"
                :closable="false"
                >Needs your help: {{ run.decision.handoff_reason }}</Message
              >
              <section
                v-if="
                  run.decision?.result &&
                  Object.keys(run.decision.result).length
                "
                class="decision-card"
              >
                <h3>How these values were matched</h3>
                <p
                  v-for="reason in run.decision.result.reasons || []"
                  :key="reason"
                >
                  {{ reason }}
                </p>
              </section>
            </div>
          </section>
        </div>
      </section>
      <DocumentViewer
        :class="{ 'mobile-pane-active': mobilePane === 'document' }"
        :pages="run.pages"
        :evidence="evidence"
        :refining="refining"
        @correct="
          (source, page, bbox) => emit('correct-bbox', source, page, bbox)
        "
      />
    </div>
    <footer class="review-completion">
      <div class="review-completion-copy">
        <strong v-if="hasUnsaved"
          ><i class="pi pi-pencil" /> Save your changes before
          continuing</strong
        >
        <button
          v-else-if="errorIssues.length"
          class="review-checks-link"
          @click="openChecks"
        >
          <i class="pi pi-exclamation-circle" /> Resolve
          {{ errorIssues.length }} required checks
          <i class="pi pi-arrow-right" />
        </button>
        <strong v-else-if="warningIssues.length"
          >Review {{ warningIssues.length }} suggestions</strong
        ><strong v-else
          ><i class="pi pi-check-circle" /> All required checks passed</strong
        >
        <details class="action-outcome">
          <summary>What happens next?</summary>
          <div>
            <p>
              {{
                action.description || "The reviewed document remains a draft."
              }}
            </p>
            <p v-if="action.result_description">
              {{ action.result_description }}
            </p>
          </div>
        </details>
      </div>
      <Button
        v-if="currentPhase && run.status === 'Review'"
        :label="`Confirm ${currentPhase.label} and continue`"
        icon="pi pi-check"
        :disabled="hasUnsaved || !!saving"
        :loading="saving === 'phase:confirm'"
        @click="advancePhase('confirm')"
      />
      <Button
        v-else-if="!['Created', 'Attached'].includes(run.status)"
        class="create-document-action"
        :label="action.label || 'Create draft'"
        icon="pi pi-check"
        :disabled="!action.ready || hasUnsaved"
        :loading="saving === 'create'"
        @click="$emit('create')"
      /><Button
        v-else
        label="Back to documents"
        outlined
        @click="$emit('new')"
      />
    </footer>
  </section>
</template>

<style scoped>
.phase-progress {
  padding: 1rem 1.4rem;
  border-bottom: 1px solid var(--surface-border, #ddd);
  background: var(--surface-card, white);
}
.phase-progress nav {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.phase-progress nav button {
  border: 1px solid #ccd5df;
  background: transparent;
  border-radius: 6px;
  padding: 0.45rem 0.65rem;
  cursor: pointer;
  text-align: left;
}
.phase-progress nav button.current {
  border-color: #176b62;
  background: #edf7f4;
}
.phase-progress nav button.complete {
  color: #176b62;
}
.phase-progress p {
  font-size: 0.88rem;
  margin: 0.55rem 0;
}
</style>

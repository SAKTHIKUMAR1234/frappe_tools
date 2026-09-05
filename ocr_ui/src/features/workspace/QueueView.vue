<script setup>
import { computed, onUnmounted, reactive, ref, watch } from "vue";
import Button from "primevue/button";
import ProgressSpinner from "primevue/progressspinner";
const props = defineProps({
  queue: {
    type: Object,
    default: () => ({ items: [], counts: {}, status: "all", loading: false }),
  },
  doctypes: { type: Array, default: () => [] },
  processing: { type: Object, default: () => ({}) },
});
const emit = defineEmits([
  "new",
  "open",
  "retry",
  "open-created",
  "refresh",
  "query",
  "status",
]);
const filters = reactive({
  search: "",
  target_doctype: "",
  created: "all",
  date_from: "",
  date_to: "",
  sort_by: "modified",
  sort_order: "desc",
  ...props.queue.filters,
});
const filtersOpen = ref(false);
let searchTimer;
// Filters belong to the user's current draft. An earlier request or background
// refresh must never overwrite text entered while that response was in flight.
watch(
  () => filters.search,
  () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => query({ start: 0 }), 300);
  },
  { flush: "sync" },
);
onUnmounted(() => clearTimeout(searchTimer));
const statuses = [
  { key: "all", label: "All documents" },
  { key: "ready", label: "To review" },
  { key: "pending", label: "Processing" },
  { key: "validated", label: "Completed" },
  { key: "error", label: "Needs attention" },
  { key: "draft", label: "Drafts" },
];
const available = computed(() =>
  props.doctypes.filter((item) => item.can_create),
);
const pageNumber = computed(
  () =>
    Math.floor(
      Number(props.queue.start || 0) / Number(props.queue.page_length || 25),
    ) + 1,
);
const pageCount = computed(() =>
  Math.max(
    1,
    Math.ceil(
      Number(props.queue.total || 0) / Number(props.queue.page_length || 25),
    ),
  ),
);
const filtered = computed(
  () =>
    filters.search ||
    filters.target_doctype ||
    filters.created !== "all" ||
    filters.date_from ||
    filters.date_to ||
    props.queue.status !== "all",
);
function query(extra = {}) {
  emit("query", { ...filters, ...extra });
}
function statusLabel(item) {
  if (item.status === "Classification Handoff") return "Choose document type";
  if (item.status === "Layout Handoff") return "Choose layout";
  if (item.job_state === "stale") return "Processing stopped";
  if (item.decision_pending) return "Verifying";
  if (item.status === "Review") return "Ready to review";
  if (["Created", "Attached"].includes(item.status)) return "Completed";
  if (item.status === "Failed") return "Needs attention";
  return item.status;
}
function formatDate(value) {
  if (!value) return "—";
  const date = new Date(String(value).replace(" ", "T"));
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      }).format(date);
}
function clearFilters() {
  Object.assign(filters, {
    search: "",
    target_doctype: "",
    created: "all",
    date_from: "",
    date_to: "",
  });
  clearTimeout(searchTimer);
  query({ start: 0, status: "all" });
}
</script>

<template>
  <section class="desk-page">
    <header class="desk-heading">
      <div>
        <h1>Documents</h1>
        <p>Add a document, review its details, then confirm the entry.</p>
      </div>
    </header>
    <section
      v-if="available.length"
      class="launch-workspace"
      aria-label="Start a document"
    >
      <div class="workflow-launches">
        <button
          v-for="item in available"
          :key="item.doctype"
          class="workflow-launch"
          @click="$emit('new', item.doctype)"
        >
          <span class="workflow-symbol"
            ><i :class="item.workflow?.icon || 'pi pi-file'"
          /></span>
          <span
            ><small>NEW DOCUMENT</small
            ><strong>{{ item.label || item.doctype }}</strong
            ><span>{{
              item.workflow?.description ||
              "Extract, verify and review your document."
            }}</span></span
          >
          <i class="pi pi-arrow-up-right" aria-hidden="true" />
        </button>
      </div>
    </section>
    <section class="document-inbox" aria-label="Document inbox">
      <header class="inbox-heading">
        <div>
          <h2>Documents</h2>
          <span>{{ queue.counts?.all || 0 }} in this workspace</span>
        </div>
        <Button
          label="Refresh"
          icon="pi pi-refresh"
          text
          size="small"
          :loading="queue.loading"
          @click="$emit('refresh')"
        />
      </header>
      <nav class="inbox-tabs" aria-label="Document status">
        <button
          v-for="status in statuses"
          :key="status.key"
          :class="{ active: queue.status === status.key }"
          :aria-pressed="queue.status === status.key"
          @click="$emit('status', status.key)"
        >
          {{ status.label }}<span>{{ queue.counts?.[status.key] || 0 }}</span>
        </button>
      </nav>
      <div class="inbox-toolbar">
        <label class="queue-search"
          ><i class="pi pi-search" /><input
            v-model="filters.search"
            aria-label="Search documents"
            placeholder="Find a document or draft…"
        /></label>
        <select
          v-model="filters.target_doctype"
          aria-label="Document type filter"
          @change="query({ start: 0 })"
        >
          <option value="">All types</option>
          <option
            v-for="item in doctypes"
            :key="item.doctype"
            :value="item.doctype"
          >
            {{ item.label || item.doctype }}
          </option>
        </select>
        <button
          class="quiet-button"
          :aria-expanded="filtersOpen"
          aria-controls="document-filters"
          @click="filtersOpen = !filtersOpen"
        >
          <i class="pi pi-sliders-h" /> Filters
        </button>
      </div>
      <div v-if="filtersOpen" id="document-filters" class="expanded-filters">
        <label
          >Result<select
            v-model="filters.created"
            @change="query({ start: 0 })"
          >
            <option value="all">All results</option>
            <option value="yes">Draft created</option>
            <option value="no">Not created</option>
          </select></label
        >
        <label
          >From<input
            v-model="filters.date_from"
            type="date"
            @change="query({ start: 0 })" /></label
        ><label
          >To<input
            v-model="filters.date_to"
            type="date"
            @change="query({ start: 0 })"
        /></label>
        <label
          >Sort by<select
            v-model="filters.sort_by"
            @change="query({ start: 0 })"
          >
            <option value="modified">Last activity</option>
            <option value="creation">Upload date</option>
            <option value="target_doctype">Document type</option>
            <option value="status">Status</option>
          </select></label
        >
        <Button
          :label="
            filters.sort_order === 'asc' ? 'Oldest first' : 'Newest first'
          "
          icon="pi pi-sort-alt"
          text
          @click="
            filters.sort_order = filters.sort_order === 'asc' ? 'desc' : 'asc';
            query({ start: 0 });
          "
        />
      </div>
      <div
        v-if="queue.loading && !queue.items?.length"
        class="queue-loading"
        role="status"
      >
        <ProgressSpinner stroke-width="3" /><span>Loading your documents…</span>
      </div>
      <div v-else-if="!queue.items?.length" class="inbox-empty">
        <span class="empty-document-symbol"><i class="pi pi-inbox" /></span>
        <h3>
          {{
            filtered
              ? "No documents match these filters."
              : "A clean desk. A fresh start."
          }}
        </h3>
        <p>
          {{
            filtered
              ? "Try another search or clear your filters."
              : "Your scans and review tasks will appear here. Choose a workflow above to begin."
          }}
        </p>
        <button v-if="filtered" class="text-link" @click="clearFilters">
          Clear filters
        </button>
        <Button
          v-else-if="available.length"
          label="Scan a document"
          icon="pi pi-plus"
          outlined
          @click="$emit('new')"
        />
      </div>
      <div v-else class="document-rows">
        <div class="document-row column-labels" aria-hidden="true">
          <span>DOCUMENT</span><span>WORKFLOW</span><span>STATUS</span
          ><span>LAST ACTIVITY</span><span />
        </div>
        <article
          v-for="item in queue.items"
          :key="item.name"
          class="document-row"
        >
          <button class="document-name" @click="$emit('open', item.name)">
            <span class="file-symbol"><i class="pi pi-file" /></span
            ><span
              ><strong>{{ item.file_name || item.name }}</strong
              ><small
                >{{ item.name }} · {{ item.page_count }} page{{
                  item.page_count === 1 ? "" : "s"
                }}</small
              ></span
            >
          </button>
          <div class="row-workflow">
            <strong>{{
              item.target_doctype || "Awaiting classification"
            }}</strong
            ><button
              v-if="item.can_open_created"
              class="text-link"
              @click="$emit('open-created', item)"
            >
              {{ item.created_document }}
              <i class="pi pi-external-link" /></button
            ><small v-else>{{ item.job_label || "No draft created" }}</small>
          </div>
          <span class="status-pill" :class="item.queue_status || 'draft'"
            ><span />{{ statusLabel(item) }}</span
          >
          <time>{{ formatDate(item.modified) }}</time>
          <Button
            v-if="item.can_review"
            label="Review"
            icon="pi pi-arrow-right"
            icon-pos="right"
            size="small"
            outlined
            @click="$emit('open', item.name)"
          /><Button
            v-else-if="item.can_retry"
            label="Retry"
            icon="pi pi-refresh"
            size="small"
            outlined
            severity="danger"
            @click="$emit('retry', item)"
          /><Button
            v-else
            label="Open"
            size="small"
            text
            @click="$emit('open', item.name)"
          />
        </article>
      </div>
      <footer v-if="queue.total" class="queue-pagination">
        <span>{{ queue.total }} documents</span>
        <div>
          <Button
            label="Previous"
            icon="pi pi-chevron-left"
            text
            size="small"
            :disabled="!queue.has_previous"
            @click="
              query({ start: Math.max(0, queue.start - queue.page_length) })
            "
          /><span>{{ pageNumber }} / {{ pageCount }}</span
          ><Button
            label="Next"
            icon="pi pi-chevron-right"
            icon-pos="right"
            text
            size="small"
            :disabled="!queue.has_more"
            @click="query({ start: queue.start + queue.page_length })"
          />
        </div>
      </footer>
    </section>
  </section>
</template>

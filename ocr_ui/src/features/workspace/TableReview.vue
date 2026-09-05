<script setup>
import { reactive, watch } from "vue";
import Button from "primevue/button";
import Message from "primevue/message";
import Tag from "primevue/tag";
import DynamicFieldInput from "./DynamicFieldInput.vue";
import { mergeDraft } from "@/services/reviewDrafts";

const props = defineProps({
  tables: { type: Array, default: () => [] },
  issues: { type: Array, default: () => [] },
  saving: { type: String, default: "" },
  drafts: { type: Object, default: () => ({}) },
});
const emit = defineEmits(["focus", "save", "resolve"]);
const rowsByTable = reactive({});

watch(
  () => props.tables,
  (tables) => {
    for (const table of tables) {
      rowsByTable[table.fieldname] = table.rows.map((row) => ({
        ...row,
        match: mergeDraft(
          props.drafts,
          `match:${table.fieldname}:${row.row_no}`,
          {
            value: row.matched_value || "",
          },
        ),
        values: mergeDraft(
          props.drafts,
          `row:${table.fieldname}:${row.row_no}`,
          row.values,
        ),
      }));
    }
  },
  { immediate: true, deep: true },
);

function rowIssues(table, row) {
  const prefix = `table:${table.fieldname}:${row.row_no}:`;
  return props.issues.filter((issue) => issue.path.startsWith(prefix));
}

function focusEvidence(table, row, column = null) {
  const cell = column ? row.cell_evidence?.[column.key] : null;
  emit("focus", {
    ...row,
    table: table.fieldname,
    column: column?.key,
    label: column?.label || table.label,
    source_page: cell?.page || row.source_page,
    bbox: cell?.x != null ? cell : row.bbox,
  });
}

function topCandidate(row) {
  return (row.candidates || [])[0] || null;
}
function score(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}
function openCandidate(table, candidate) {
  if (!candidate?.value || !table.resolver?.link_doctype) return;
  window.open(
    `/app/${encodeURIComponent(table.resolver.link_doctype.toLowerCase().replace(/\s+/g, "-"))}/${encodeURIComponent(candidate.value)}`,
    "_blank",
    "noopener",
  );
}
</script>

<template>
  <div class="table-review-list">
    <article
      v-for="table in tables"
      :key="table.fieldname"
      class="review-table-card"
    >
      <div class="table-heading">
        <div>
          <h3>{{ table.label }}</h3>
          <p v-if="table.notes">{{ table.notes }}</p>
        </div>
        <Tag
          :value="`${table.rows.length} row${table.rows.length === 1 ? '' : 's'}`"
          severity="secondary"
          rounded
        />
      </div>

      <Message v-if="!table.rows.length" severity="warn" :closable="false">
        No rows were found in this table.
      </Message>

      <div v-else class="extracted-row-list">
        <section
          v-for="data in rowsByTable[table.fieldname]"
          :key="data.row_no"
          class="extracted-row-card"
        >
          <header class="extracted-row-heading">
            <strong>Row {{ data.row_no }}</strong>
            <button type="button" @click="focusEvidence(table, data)">
              <i class="pi pi-map-marker" /> Page {{ data.source_page }}
            </button>
          </header>

          <div class="extracted-row-fields">
            <div
              v-for="column in table.columns"
              :key="column.key"
              class="extracted-cell"
            >
              <label
                :for="`cell-${table.fieldname}-${data.row_no}-${column.key}`"
                >{{ column.label }}</label
              >
              <div>
                <DynamicFieldInput
                  :id="`cell-${table.fieldname}-${data.row_no}-${column.key}`"
                  v-model="data.values[column.key]"
                  :field="column"
                  :disabled="
                    saving === `table:${table.fieldname}:${data.row_no}`
                  "
                />
              </div>
              <button
                type="button"
                class="source-link"
                :aria-label="`View source for ${column.label}, row ${data.row_no}`"
                @click="focusEvidence(table, data, column)"
              >
                <i class="pi pi-eye" /> View source
              </button>
            </div>
          </div>

          <section v-if="table.resolver" class="record-match-card">
            <div class="record-match-heading">
              <div>
                <label :for="`match-${table.fieldname}-${data.row_no}`">{{
                  table.resolver.label || "Mapped record"
                }}</label>
                <small>Confirm the local record this row belongs to</small>
              </div>
              <Tag
                v-if="topCandidate(data)"
                :value="`${score(topCandidate(data).score)} match`"
                :severity="topCandidate(data).eligible ? 'success' : 'warn'"
              />
            </div>
            <div class="record-match-control">
              <DynamicFieldInput
                :id="`match-${table.fieldname}-${data.row_no}`"
                v-model="data.match.value"
                :field="{
                  fieldtype: 'Link',
                  link_doctype: table.resolver.link_doctype,
                  link_query: table.resolver.link_query,
                  label: table.resolver.label,
                }"
                :initial-suggestions="data.candidates || []"
                :disabled="
                  saving === `resolve:${table.fieldname}:${data.row_no}`
                "
              />
              <Button
                v-if="data.match.value"
                label="Use match"
                icon="pi pi-link"
                size="small"
                outlined
                @click.stop="
                  $emit(
                    'resolve',
                    table,
                    data,
                    data.match.value?.value || data.match.value,
                  )
                "
              />
            </div>
            <slot name="row-actions" :table="table" :row="data" />
            <small v-if="data.master_proposal" class="resolution-note">
              Proposal {{ data.master_proposal }} awaits approval
            </small>
            <div v-if="topCandidate(data)?.reason" class="match-explanation">
              <button
                type="button"
                @click.stop="openCandidate(table, topCandidate(data))"
              >
                {{ topCandidate(data).label }}
                <i
                  v-if="table.resolver?.link_doctype"
                  class="pi pi-external-link"
                />
              </button>
              <small>{{ topCandidate(data).reason }}</small>
              <div
                v-if="table.resolver.score_fields?.length"
                class="match-score-grid"
              >
                <span
                  v-for="metric in table.resolver.score_fields"
                  :key="metric.key"
                  >{{ metric.label }}
                  {{ score(topCandidate(data)[metric.key]) }}</span
                >
              </div>
            </div>
          </section>

          <div v-if="rowIssues(table, data).length" class="row-checks">
            <p
              v-for="issue in rowIssues(table, data)"
              :key="issue.message"
              class="inline-issue"
              :class="issue.severity"
            >
              <i
                :class="
                  issue.severity === 'error'
                    ? 'pi pi-exclamation-circle'
                    : 'pi pi-info-circle'
                "
              />
              {{ issue.message }}
            </p>
          </div>

          <footer class="extracted-row-actions">
            <span>Check the printed values and selected local record.</span>
            <Button
              :label="
                ['Confirmed', 'Free Text'].includes(data.status)
                  ? 'Save row'
                  : 'Confirm row'
              "
              icon="pi pi-check"
              size="small"
              :loading="saving === `table:${table.fieldname}:${data.row_no}`"
              @click.stop="$emit('save', table, data, data.values)"
            />
          </footer>
        </section>
      </div>
    </article>
  </div>
</template>

<style scoped>
.record-match-heading > div {
  display: grid;
  gap: 0.25rem;
}
.record-match-heading label {
  font-weight: 600;
}
</style>

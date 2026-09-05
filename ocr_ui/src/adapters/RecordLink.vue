<script setup>
import { computed, ref, watch } from "vue";
import AutoComplete from "primevue/autocomplete";
import { call, errorMessage } from "@/services/api";

const props = defineProps({
  modelValue: { default: "" },
  extraction: { type: String, required: true },
  query: { type: String, required: true },
  label: { type: String, required: true },
  id: { type: String, default: undefined },
  disabled: Boolean,
  suggestions: { type: Array, default: () => [] },
  revision: { type: String, default: "" },
});
const emit = defineEmits(["update:modelValue"]);
const options = ref([]);
const selected = ref(null);
const failure = ref("");
const input = computed(() =>
  selected.value?.value === props.modelValue
    ? selected.value
    : props.modelValue,
);
let version = 0;

async function lookup(text, value = null) {
  return call("frappe_tools.api.adapter_ui.search_link", {
    extraction: props.extraction,
    query: props.query,
    text,
    value,
  });
}

async function search({ query }) {
  const request = ++version;
  try {
    const rows = await lookup(query || "");
    if (request !== version) return;
    options.value = rows;
    failure.value = "";
  } catch (error) {
    if (request !== version) return;
    options.value = [];
    failure.value = errorMessage(error, "Could not search records.");
  }
}

let previewVersion = 0;
watch(
  () => [props.modelValue, props.extraction, props.query, props.revision],
  async () => {
    const request = ++previewVersion;
    selected.value = null;
    if (!props.modelValue) return;
    try {
      const rows = await lookup("", props.modelValue);
      if (request !== previewVersion) return;
      selected.value = rows[0] || null;
      failure.value = rows.length
        ? ""
        : "Choose an available record from the list.";
    } catch (error) {
      if (request === previewVersion) failure.value = errorMessage(error);
    }
  },
  { immediate: true },
);

function update(value) {
  selected.value = value && typeof value === "object" ? value : null;
  emit("update:modelValue", selected.value?.value || value || "");
}
</script>

<template>
  <div class="adapter-record-link">
    <label v-if="!id" :for="`adapter-link-${query}`">{{ label }}</label>
    <AutoComplete
      :input-id="id || `adapter-link-${query}`"
      :model-value="input"
      :suggestions="options"
      option-label="label"
      :disabled="disabled"
      dropdown
      fluid
      @complete="search"
      @update:model-value="update"
    >
      <template #option="{ option }">
        <div class="adapter-link-option">
          <strong>{{ option.label }}</strong>
          <small v-if="option.label !== option.value">{{ option.value }}</small>
          <small v-for="detail in option.details" :key="detail.label"
            >{{ detail.label }}: {{ detail.value }}</small
          >
        </div>
      </template>
    </AutoComplete>
    <p v-if="failure" class="inline-issue" role="status">{{ failure }}</p>
    <dl v-if="selected" class="adapter-record-preview">
      <dt>Record ID</dt>
      <dd>{{ selected.value }}</dd>
      <template v-for="detail in selected.details" :key="detail.label">
        <dt>{{ detail.label }}</dt>
        <dd>{{ detail.value }}</dd>
      </template>
    </dl>
    <div v-if="suggestions.length" class="adapter-suggestions">
      <span>Suggested matches</span>
      <button
        v-for="option in suggestions"
        :key="option.value"
        type="button"
        :disabled="disabled"
        @click="update(option)"
      >
        {{ option.label || option.value
        }}<small v-if="option.reason">{{ option.reason }}</small>
      </button>
    </div>
  </div>
</template>

<style scoped>
.adapter-record-link {
  display: grid;
  gap: 0.5rem;
  min-width: 0;
}
.adapter-link-option {
  display: grid;
  gap: 0.25rem;
  padding: 0.25rem 0;
}
.adapter-link-option small {
  white-space: normal;
}
.adapter-record-preview {
  display: grid;
  grid-template-columns: minmax(5rem, 1fr) 2fr;
  gap: 0.35rem 1rem;
  margin: 0.25rem 0;
  font-size: 0.85rem;
}
.adapter-record-preview dt {
  color: #667085;
}
.adapter-record-preview dd {
  margin: 0;
  overflow-wrap: anywhere;
  white-space: pre-line;
}
.adapter-suggestions {
  display: grid;
  gap: 0.35rem;
}
.adapter-suggestions button {
  text-align: left;
  border: 1px solid #d0d5dd;
  border-radius: 0.4rem;
  padding: 0.5rem;
  background: white;
  cursor: pointer;
}
.adapter-suggestions small {
  display: block;
  color: #667085;
}
</style>

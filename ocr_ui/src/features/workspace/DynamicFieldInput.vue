<script setup>
import { computed, inject, ref, watch } from "vue";
import AutoComplete from "primevue/autocomplete";
import InputNumber from "primevue/inputnumber";
import InputText from "primevue/inputtext";
import Select from "primevue/select";
import ToggleSwitch from "primevue/toggleswitch";
import { useOcrWorkspace } from "./useOcrWorkspace";
import RecordLink from "@/adapters/RecordLink.vue";

const props = defineProps({
  id: { type: String, default: undefined },
  modelValue: { default: "" },
  field: { type: Object, required: true },
  initialSuggestions: { type: Array, default: () => [] },
  disabled: Boolean,
});
const emit = defineEmits(["update:modelValue"]);
const { searchLink } = useOcrWorkspace();
const adapter = inject("document-adapter", null);
const localValue = ref(props.modelValue);
const suggestions = ref([...props.initialSuggestions]);

watch(
  () => props.initialSuggestions,
  (value) => {
    suggestions.value = [...(value || [])];
  },
  { deep: true },
);

watch(
  () => props.modelValue,
  (value) => {
    const current =
      localValue.value && typeof localValue.value === "object"
        ? localValue.value.value
        : localValue.value;
    if (current !== value) localValue.value = value;
  },
);

const numericTypes = new Set(["Int", "Float", "Currency", "Percent", "number"]);
const numericValue = computed(() => {
  if (localValue.value === "" || localValue.value == null) return null;
  const value = Number(localValue.value);
  return Number.isFinite(value) ? value : null;
});

function update(value) {
  localValue.value = value;
  const normalized = value && typeof value === "object" ? value.value : value;
  emit("update:modelValue", normalized);
}

let searchVersion = 0;
async function complete(event) {
  const version = ++searchVersion;
  let remote;
  try {
    remote = await searchLink(props.field.link_doctype, event.query || "");
  } catch {
    remote = [];
  }
  if (version !== searchVersion) return;
  const combined = [...props.initialSuggestions, ...remote];
  suggestions.value = combined.filter(
    (item, index) =>
      item?.value &&
      combined.findIndex((candidate) => candidate?.value === item.value) ===
        index,
  );
}
</script>

<template>
  <RecordLink
    v-if="field.link_query && adapter"
    :id="id"
    :model-value="modelValue"
    :extraction="adapter.run.name"
    :query="field.link_query"
    :label="field.label || 'Record'"
    :disabled="disabled"
    :suggestions="initialSuggestions"
    :revision="adapter.run.modified"
    @update:model-value="update"
  />
  <AutoComplete
    :input-id="id"
    v-else-if="field.link_doctype"
    :model-value="localValue"
    :suggestions="suggestions"
    option-label="label"
    dropdown
    fluid
    :disabled="disabled"
    @complete="complete"
    @update:model-value="update"
  />
  <Select
    :input-id="id"
    v-else-if="field.fieldtype === 'Select' || field.type === 'Select'"
    :model-value="localValue"
    :options="field.options || []"
    show-clear
    fluid
    :disabled="disabled"
    @update:model-value="update"
  />
  <InputNumber
    :input-id="id"
    v-else-if="numericTypes.has(field.fieldtype || field.type)"
    :model-value="numericValue"
    :min-fraction-digits="(field.fieldtype || field.type) === 'Int' ? 0 : 0"
    :max-fraction-digits="(field.fieldtype || field.type) === 'Int' ? 0 : 6"
    fluid
    :disabled="disabled"
    @update:model-value="update"
  />
  <ToggleSwitch
    :input-id="id"
    v-else-if="(field.fieldtype || field.type) === 'Check'"
    :model-value="Boolean(Number(localValue))"
    :disabled="disabled"
    @update:model-value="
      (value) => {
        update(value ? 1 : 0);
      }
    "
  />
  <InputText
    :id="id"
    v-else
    :model-value="localValue"
    fluid
    :disabled="disabled"
    @update:model-value="update"
  />
</template>

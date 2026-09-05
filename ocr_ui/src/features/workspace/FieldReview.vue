<script setup>
import { reactive, watch } from "vue";
import Button from "primevue/button";
import Tag from "primevue/tag";
import DynamicFieldInput from "./DynamicFieldInput.vue";
import { mergeDraft } from "@/services/reviewDrafts";

const props = defineProps({
  fields: { type: Array, default: () => [] },
  issues: { type: Array, default: () => [] },
  saving: { type: String, default: "" },
  drafts: { type: Object, default: () => ({}) },
});
const emit = defineEmits(["focus", "save"]);
const values = reactive({});

watch(
  () => props.fields,
  (fields) => {
    for (const field of fields) {
      const draft = mergeDraft(props.drafts, `field:${field.fieldname}`, {
        value: field.value ?? "",
      });
      values[field.fieldname] = draft;
    }
  },
  { immediate: true, deep: true },
);

function severity(field) {
  if (
    props.issues.some(
      (issue) =>
        issue.path === `field:${field.fieldname}` && issue.severity === "error",
    )
  )
    return "danger";
  if (field.status === "Edited" || field.status === "Approved")
    return "success";
  if (field.confidence < 0.65) return "warn";
  return "secondary";
}

function label(field) {
  if (field.status === "Approved") return "Confirmed";
  if (field.status === "Edited") return "Edited";
  if (["danger", "warn"].includes(severity(field))) return "Needs review";
  return field.value === null || field.value === undefined || field.value === ""
    ? "Needs input"
    : "Extracted";
}

function isDirty(field) {
  return (
    String(values[field.fieldname]?.value ?? "") !== String(field.value ?? "")
  );
}
</script>

<template>
  <div class="field-review-list">
    <div v-for="field in fields" :key="field.fieldname" class="review-field">
      <div class="field-label-cell">
        <div class="review-field-heading">
          <label :for="`field-${field.fieldname}`">
            {{ field.label }}
            <span v-if="field.required" class="required-mark">*</span>
          </label>
          <Tag :value="label(field)" :severity="severity(field)" />
        </div>
      </div>
      <div class="field-value-cell">
        <DynamicFieldInput
          :id="`field-${field.fieldname}`"
          v-model="values[field.fieldname].value"
          :field="field"
          :disabled="saving === `field:${field.fieldname}`"
        />
        <div class="field-review-actions">
          <Button
            v-if="field.input_source !== 'local'"
            label="View source"
            icon="pi pi-eye"
            size="small"
            outlined
            severity="secondary"
            :aria-label="`View source for ${field.label}`"
            @click="$emit('focus', field)"
          />
          <Button
            v-if="
              isDirty(field) ||
              (!['Approved', 'Edited'].includes(field.status) &&
                values[field.fieldname].value !== '' &&
                values[field.fieldname].value != null)
            "
            :label="isDirty(field) ? 'Save change' : 'Confirm'"
            :aria-label="`${isDirty(field) ? 'Save' : 'Confirm'} ${field.label}`"
            icon="pi pi-check"
            size="small"
            outlined
            :loading="saving === `field:${field.fieldname}`"
            @click="$emit('save', field, values[field.fieldname].value)"
          />
        </div>
        <div class="field-evidence-copy">
          <span v-if="field.bbox?.source === 'manual'"
            ><i class="pi pi-check-circle" /> Reviewer verified source</span
          >
          <span v-else-if="field.bbox?.source === 'ocr'"
            ><i class="pi pi-sparkles" /> OCR verified<span
              v-if="field.raw_text"
              >: “{{ field.raw_text }}”</span
            ></span
          >
          <span v-else-if="field.raw_text">Source: “{{ field.raw_text }}”</span>
          <span v-else
            ><i class="pi pi-eye-slash" /> No verified OCR evidence</span
          >
        </div>
        <p
          v-for="issue in issues.filter(
            (item) => item.path === `field:${field.fieldname}`,
          )"
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
    </div>
  </div>
</template>

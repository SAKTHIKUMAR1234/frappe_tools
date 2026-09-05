<script setup>
import {
  computed,
  markRaw,
  onErrorCaptured,
  provide,
  ref,
  shallowRef,
  watch,
} from "vue";
import Message from "primevue/message";
import ReviewWorkspace from "../features/workspace/ReviewWorkspace.vue";
import registry from "virtual:document-adapters";
import { call } from "@/services/api";
import { useOcrWorkspace } from "../features/workspace/useOcrWorkspace";

defineOptions({ inheritAttrs: false });
const props = defineProps({
  run: { type: Object, required: true },
  saving: { type: String, default: "" },
});
const emit = defineEmits(["dirty-change"]);
const extension = shallowRef({});
const failure = ref("");
const loading = ref(false);
const dirty = ref(false);
const { state, refreshRun } = useOcrWorkspace();
const context = {
  get run() {
    return props.run;
  },
  get saving() {
    return props.saving;
  },
  get dirty() {
    return dirty.value;
  },
  async phase(phase, operation) {
    if (dirty.value)
      throw new Error("Save your review changes before continuing.");
    if (state.saving) throw new Error("Wait for the current save to finish.");
    state.saving = `phase:${operation}`;
    try {
      const result = await call("frappe_tools.api.adapter_ui.advance_phase", {
        extraction: props.run.name,
        modified: props.run.modified,
        phase,
        operation,
      });
      await refreshRun();
      return result;
    } finally {
      state.saving = "";
    }
  },
  can(action) {
    return Boolean(props.run.adapter_ui?.actions?.[action]?.allowed);
  },
  async action(action, values = {}) {
    if (dirty.value)
      throw new Error("Save your review changes before continuing.");
    if (state.saving) throw new Error("Wait for the current save to finish.");
    state.saving = `adapter:${action}`;
    try {
      const result = await call("frappe_tools.api.adapter_ui.run_action", {
        extraction: props.run.name,
        modified: props.run.modified,
        action,
        values,
      });
      await refreshRun();
      return result;
    } finally {
      state.saving = "";
    }
  },
};
provide("document-adapter", context);
const component = computed(() => extension.value.Review || ReviewWorkspace);
let loadVersion = 0;
watch(
  () => props.run.adapter_ui?.module,
  async (module) => {
    const request = ++loadVersion;
    extension.value = {};
    failure.value = "";
    loading.value = Boolean(module);
    if (!module) return;
    try {
      if (!registry[module]) throw new Error("Missing adapter build");
      const loaded = await registry[module]();
      if (request === loadVersion)
        extension.value = markRaw(loaded.default || {});
    } catch {
      if (request === loadVersion)
        failure.value =
          "This document's review interface could not load. Ask your administrator to build the installed adapter, then reload this page.";
    } finally {
      if (request === loadVersion) loading.value = false;
    }
  },
  { immediate: true },
);
onErrorCaptured(() => {
  failure.value =
    "This document's review interface encountered an error. Reload the page to continue.";
  return false;
});
function updateDirty(value) {
  dirty.value = value;
  emit("dirty-change", value);
}
</script>

<template>
  <Message v-if="failure" severity="error" :closable="false">{{
    failure
  }}</Message>
  <p v-else-if="loading" role="status">Loading review…</p>
  <component
    v-else
    :is="component"
    v-bind="$attrs"
    :run="run"
    :saving="saving"
    :adapter="extension"
    :context="context"
    @dirty-change="updateDirty"
  />
</template>

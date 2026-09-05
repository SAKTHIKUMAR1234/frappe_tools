<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import Button from "primevue/button";
import Tag from "primevue/tag";
import Select from "primevue/select";

const props = defineProps({
  pages: { type: Array, default: () => [] },
  evidence: { type: Object, default: null },
  refining: Boolean,
});
const emit = defineEmits(["correct"]);

const pageNumber = ref(props.pages[0]?.page_no || 1);
const zoom = ref(1);
const adjusting = ref(false);
const draftBox = ref(null);
const dragStart = ref(null);
const viewport = ref(null);
const sheet = ref(null);
const viewportSize = ref({ width: 640, height: 800 });
let resizeObserver;
onMounted(() => {
  resizeObserver = new ResizeObserver(([entry]) => {
    if (entry.contentRect.width > 0 && entry.contentRect.height > 0)
      viewportSize.value = entry.contentRect;
  });
  if (viewport.value) resizeObserver.observe(viewport.value);
});
onUnmounted(() => resizeObserver?.disconnect());
const currentPage = computed(
  () =>
    props.pages.find((page) => page.page_no === pageNumber.value) ||
    props.pages[0],
);
const aspectRatio = computed(() => {
  const page = currentPage.value;
  return page?.width && page?.height
    ? `${page.width} / ${page.height}`
    : "3 / 4";
});
const sheetWidth = computed(() => {
  const ratio =
    currentPage.value?.width && currentPage.value?.height
      ? currentPage.value.width / currentPage.value.height
      : 0.75;
  const fit = Math.min(
    viewportSize.value.width - 32,
    (viewportSize.value.height - 32) * ratio,
  );
  return Math.max(120, fit) * zoom.value;
});
const pageChoices = computed(() =>
  props.pages.map((page, index) => ({
    ...page,
    label: `${index + 1} of ${props.pages.length}`,
  })),
);
const visibleBox = computed(() =>
  props.evidence?.source_page === pageNumber.value ? props.evidence.bbox : null,
);
function paddedBox(box) {
  if (!box) return null;
  const x = Number(box.x || 0);
  const y = Number(box.y || 0);
  const w = Number(box.w || 0);
  const h = Number(box.h || 0);
  // Keep the evidence border outside the recognized glyphs. The stored box is
  // never changed; padding is only a visual review aid.
  const paddingX = Math.max(0.006, Math.min(0.015, w * 0.12));
  const paddingY = Math.max(0.004, Math.min(0.012, h * 0.3));
  const left = Math.max(0, x - paddingX);
  const top = Math.max(0, y - paddingY);
  return {
    ...box,
    x: left,
    y: top,
    w: Math.min(1 - left, w + paddingX * 2),
    h: Math.min(1 - top, h + paddingY * 2),
  };
}
const displayBox = computed(() =>
  draftBox.value ? draftBox.value : paddedBox(visibleBox.value),
);

watch(
  () => [
    props.evidence?.source_page,
    props.evidence?.bbox?.x,
    props.evidence?.bbox?.y,
  ],
  ([page]) => {
    if (page) pageNumber.value = page;
    adjusting.value = false;
    draftBox.value = null;
    nextTick(centerEvidence);
  },
  { immediate: true },
);

function centerEvidence() {
  const box = paddedBox(visibleBox.value);
  if (!box || !viewport.value || !sheet.value) return;
  // Centre the whole evidence area, not its top-left corner. This keeps even
  // narrow values visible after the automatic review zoom.
  const viewportRect = viewport.value.getBoundingClientRect();
  const sheetRect = sheet.value.getBoundingClientRect();
  const left =
    sheetRect.left -
    viewportRect.left +
    viewport.value.scrollLeft +
    (Number(box.x || 0) + Number(box.w || 0) / 2) * sheetRect.width;
  const top =
    sheetRect.top -
    viewportRect.top +
    viewport.value.scrollTop +
    (Number(box.y || 0) + Number(box.h || 0) / 2) * sheetRect.height;
  viewport.value.scrollTo({
    left: Math.max(0, left - viewport.value.clientWidth / 2),
    top: Math.max(0, top - viewport.value.clientHeight / 2),
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "instant"
      : "smooth",
  });
}

function zoomBy(delta) {
  zoom.value = Math.min(
    3,
    Math.max(0.7, Number((zoom.value + delta).toFixed(1))),
  );
  nextTick(centerEvidence);
}

function point(event) {
  const rect = event.currentTarget.getBoundingClientRect();
  return {
    x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
    y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
  };
}

function startDrawing(event) {
  if (!adjusting.value) return;
  event.preventDefault();
  event.currentTarget.setPointerCapture?.(event.pointerId);
  dragStart.value = point(event);
  draftBox.value = { ...dragStart.value, w: 0, h: 0 };
}

function draw(event) {
  if (!dragStart.value) return;
  const end = point(event);
  draftBox.value = {
    x: Math.min(dragStart.value.x, end.x),
    y: Math.min(dragStart.value.y, end.y),
    w: Math.abs(end.x - dragStart.value.x),
    h: Math.abs(end.y - dragStart.value.y),
    source: "manual",
  };
}

function finishDrawing(event) {
  if (!dragStart.value) return;
  draw(event);
  const box = draftBox.value;
  dragStart.value = null;
  if (box.w >= 0.002 && box.h >= 0.002) {
    emit("correct", props.evidence, pageNumber.value, box);
    adjusting.value = false;
  }
}

function toggleAdjusting() {
  adjusting.value = !adjusting.value;
  draftBox.value = null;
  dragStart.value = null;
}
</script>

<template>
  <section class="document-viewer">
    <header class="viewer-toolbar">
      <div>
        <strong>Source document</strong>
      </div>
      <div class="viewer-actions">
        <Select
          v-model="pageNumber"
          :options="pageChoices"
          option-value="page_no"
          option-label="label"
          aria-label="Document page"
          @change="zoom = 1"
        />
        <Button
          icon="pi pi-minus"
          text
          aria-label="Zoom out"
          @click="zoomBy(-0.2)"
        />
        <Tag :value="`${Math.round(zoom * 100)}%`" severity="secondary" />
        <Button
          icon="pi pi-plus"
          text
          aria-label="Zoom in"
          @click="zoomBy(0.2)"
        />
        <Button
          icon="pi pi-refresh"
          text
          aria-label="Fit full page"
          @click="zoom = 1"
        />
      </div>
    </header>

    <div ref="viewport" class="document-viewport">
      <div
        ref="sheet"
        v-if="currentPage"
        class="document-sheet"
        :class="{ adjusting }"
        :style="{ aspectRatio, width: `${Math.round(sheetWidth)}px` }"
        @pointerdown="startDrawing"
        @pointermove="draw"
        @pointerup="finishDrawing"
        @pointercancel="dragStart = null"
      >
        <img
          :src="currentPage.image"
          :alt="`Document page ${currentPage.page_no}`"
        />
        <span
          v-if="displayBox"
          class="evidence-box"
          :class="displayBox.source || 'model'"
          :style="{
            left: `${displayBox.x * 100}%`,
            top: `${displayBox.y * 100}%`,
            width: `${displayBox.w * 100}%`,
            height: `${displayBox.h * 100}%`,
          }"
        />
        <span v-if="adjusting && !dragStart" class="draw-instruction"
          >Drag over the exact printed or handwritten value</span
        >
      </div>
    </div>
    <footer class="viewer-evidence-footer">
      <div class="viewer-evidence-caption">
        <strong
          >{{ evidence?.label || "Document evidence" }} · Page
          {{ pageNumber }}</strong
        >
        <span v-if="refining" class="grounding-state"
          ><i class="pi pi-spin pi-spinner" /> Matching text</span
        >
        <span
          v-else-if="visibleBox"
          class="grounding-state"
          :class="visibleBox.source || 'model'"
          :title="visibleBox.matched_text || 'Source evidence status'"
        >
          {{
            visibleBox.source === "manual"
              ? "Reviewer verified"
              : visibleBox.source === "ocr"
                ? `OCR verified${visibleBox.score ? ` · ${Math.round(visibleBox.score * 100)}%` : ""}`
                : "Needs verification"
          }}
        </span>
        <span v-else-if="evidence" class="grounding-state missing">
          <i class="pi pi-exclamation-triangle" /> No verified source
        </span>
      </div>
      <Button
        v-if="evidence"
        class="correct-box-button"
        icon="pi pi-pencil"
        :label="
          adjusting
            ? 'Cancel'
            : visibleBox?.source === 'ocr' || visibleBox?.source === 'manual'
              ? 'Adjust highlight'
              : 'Verify source'
        "
        :aria-label="adjusting ? 'Cancel box correction' : 'Correct source box'"
        outlined
        size="small"
        :severity="adjusting ? 'danger' : undefined"
        :aria-pressed="adjusting"
        @click="toggleAdjusting"
      />
    </footer>
  </section>
</template>

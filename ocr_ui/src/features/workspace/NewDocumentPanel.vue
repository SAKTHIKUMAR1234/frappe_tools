<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import Button from "primevue/button";
import Dialog from "primevue/dialog";
import Message from "primevue/message";
import ProgressBar from "primevue/progressbar";
import Select from "primevue/select";
import LiveScannerBridge from "./LiveScannerBridge.vue";
import {
  disposePage,
  formatBytes,
  isPdfFile,
  makePage,
} from "@/services/images";

const props = defineProps({
  initialTarget: { type: String, default: "" },
  doctypes: { type: Array, default: () => [] },
  layouts: { type: Array, default: () => [] },
  limits: { type: Object, default: () => ({ max_pages: 20, max_file_mb: 15 }) },
  processing: {
    type: Object,
    default: () => ({ timeout_per_page_minutes: 3, queue: "long" }),
  },
  user: { type: String, default: "" },
  siteName: { type: String, default: "" },
  busy: Boolean,
  upload: {
    type: Object,
    default: () => ({ completed: 0, total: 0, label: "" }),
  },
});

const emit = defineEmits(["start", "cancel", "dirty-change"]);
const pages = ref([]);
watch(
  () => pages.value.length,
  (count) => emit("dirty-change", count > 0),
);
onUnmounted(() => emit("dirty-change", false));
const selectedTarget = ref("");
const selectedLayout = ref("");
const error = ref("");
const uploadInput = ref(null);
const cameraInput = ref(null);
const liveBridge = ref(null);
const phoneFolders = ref(new Map());
const dragging = ref(false);
const cameraOpen = ref(false);
const cameraVideo = ref(null);
const cameraStream = ref(null);
const previewPage = ref(null);

const layoutOptions = computed(() =>
  props.layouts
    .filter((item) => item.can_create)
    .map((item) => ({
      ...item,
      display: `${item.layout} · ${item.target_label || item.target_doctype}`,
    })),
);
const selectedProfile = computed(() =>
  props.layouts.find((item) => item.layout === selectedLayout.value),
);
const targetOptions = computed(() => {
  const targets = new Map();
  for (const item of layoutOptions.value) {
    if (!targets.has(item.target_doctype)) {
      targets.set(item.target_doctype, {
        doctype: item.target_doctype,
        label: item.target_label || item.target_doctype,
        icon: item.workflow?.icon || "pi pi-file",
        description:
          item.workflow?.description ||
          `Extract and review ${item.target_label || item.target_doctype}.`,
      });
    }
  }
  return [...targets.values()];
});
const visibleLayouts = computed(() =>
  layoutOptions.value.filter(
    (item) => item.target_doctype === selectedTarget.value,
  ),
);
const progress = computed(() =>
  props.upload.total
    ? Math.round((props.upload.completed / props.upload.total) * 100)
    : 0,
);
function addFiles(fileList) {
  error.value = "";
  let manualCount = pages.value.filter((page) => !page.phoneFolderId).length;
  for (const file of Array.from(fileList || [])) {
    if (!file.type.startsWith("image/") && !isPdfFile(file)) {
      error.value = `${file.name} is not a supported image or PDF.`;
      continue;
    }
    if (file.size > props.limits.max_file_mb * 1024 * 1024) {
      error.value = `${file.name} is larger than ${props.limits.max_file_mb} MB.`;
      continue;
    }
    if (manualCount >= props.limits.max_pages) {
      error.value = `A document can contain up to ${props.limits.max_pages} pages.`;
      break;
    }
    pages.value.push(makePage(file));
    manualCount += 1;
  }
}

function selectTarget(doctype) {
  selectedTarget.value = doctype;
  const current = selectedProfile.value;
  if (current?.target_doctype !== doctype) selectedLayout.value = "";
  const choices = layoutOptions.value.filter(
    (item) => item.target_doctype === doctype,
  );
  if (choices.length === 1) selectedLayout.value = choices[0].layout;
}

function receiveLive(payload) {
  if (payload?.type === "folder" && payload.folder) {
    const folder = payload.folder;
    const folderId = String(folder.folder_id || "");
    if (!folderId || !Array.isArray(folder.files)) return;
    if (folder.files.length > props.limits.max_pages) {
      error.value = `${folder.folder_name || "Phone scan"} exceeds the ${props.limits.max_pages}-page document limit.`;
      return;
    }
    const added = [];
    for (const file of folder.files.filter(Boolean)) {
      const page = makePage(file);
      page.phoneFolderId = folderId;
      added.push(page);
    }
    // A phone may retry an uncommitted folder after a connection interruption.
    // Replace the staged copy; never turn a retry into duplicate document pages.
    const previous = pages.value.filter(
      (page) => page.phoneFolderId === folderId,
    );
    pages.value = pages.value.filter((page) => page.phoneFolderId !== folderId);
    previous.forEach(disposePage);
    pages.value.push(...added);
    phoneFolders.value.set(folderId, {
      id: folderId,
      name: folder.folder_name || "Phone scan",
      targetDoctype: folder.target_doctype,
      layout: folder.layout,
      operationMode: folder.operation_mode || "Create New",
      existingDocument: folder.existing_document || "",
    });
    if (!selectedLayout.value && folder.layout) {
      selectedLayout.value = folder.layout;
      selectedTarget.value = folder.target_doctype || "";
    }
    return;
  }
  if (payload?.file) addFiles([payload.file]);
}

function onDrop(event) {
  dragging.value = false;
  addFiles(event.dataTransfer.files);
}

function rotate(page) {
  if (page.isPdf) return;
  page.rotation = (page.rotation + 90) % 360;
}

function move(index, delta) {
  const page = pages.value[index];
  const group = pages.value
    .map((candidate, candidateIndex) => ({ candidate, candidateIndex }))
    .filter(
      ({ candidate }) =>
        (candidate.phoneFolderId || "") === (page.phoneFolderId || ""),
    );
  const position = group.findIndex(
    ({ candidateIndex }) => candidateIndex === index,
  );
  const targetEntry = group[position + delta];
  if (!targetEntry) return;
  const target = targetEntry.candidateIndex;
  const copy = [...pages.value];
  [copy[index], copy[target]] = [copy[target], copy[index]];
  pages.value = copy;
}

function remove(index) {
  const [page] = pages.value.splice(index, 1);
  disposePage(page);
}

function start() {
  if (!pages.value.length) {
    error.value = "Add at least one document page.";
    return;
  }
  const manualPages = pages.value.filter((page) => !page.phoneFolderId);
  if (manualPages.length && !selectedProfile.value) {
    error.value = "Select the Document Scanner Layout for this batch.";
    return;
  }
  const batches = [];
  if (manualPages.length) {
    batches.push({
      targetDoctype: selectedProfile.value.target_doctype,
      selectedLayout: selectedProfile.value.layout,
      pages: manualPages,
    });
  }
  for (const folder of phoneFolders.value.values()) {
    const folderPages = pages.value.filter(
      (page) => page.phoneFolderId === folder.id,
    );
    if (!folderPages.length) continue;
    batches.push({
      targetDoctype: folder.targetDoctype,
      selectedLayout: folder.layout,
      operationMode: folder.operationMode,
      existingDocument: folder.existingDocument,
      pages: folderPages,
      phoneFolderId: folder.id,
    });
  }
  emit("start", {
    batches,
    onCommitted: (folderId) => liveBridge.value?.confirmFolder(folderId),
  });
}

async function openCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    cameraInput.value?.click();
    return;
  }
  try {
    cameraStream.value = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" } },
      audio: false,
    });
    cameraOpen.value = true;
    await nextTick();
    cameraVideo.value.srcObject = cameraStream.value;
    await cameraVideo.value.play();
  } catch {
    closeCamera();
    cameraInput.value?.click();
  }
}

function capturePhoto() {
  const video = cameraVideo.value;
  if (!video?.videoWidth) return;
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  canvas.toBlob(
    (blob) => {
      if (!blob) return;
      addFiles([
        new File([blob], `scan-${Date.now()}.jpg`, { type: "image/jpeg" }),
      ]);
      closeCamera();
    },
    "image/jpeg",
    0.92,
  );
}

function closeCamera() {
  cameraStream.value?.getTracks?.().forEach((track) => track.stop());
  cameraStream.value = null;
  cameraOpen.value = false;
}

onMounted(() => {
  if (props.initialTarget) selectTarget(props.initialTarget);
});

onUnmounted(() => {
  closeCamera();
  pages.value.forEach(disposePage);
});
</script>

<template>
  <section class="capture-flow">
    <header class="flow-heading">
      <button class="back-link" :disabled="busy" @click="$emit('cancel')">
        <i class="pi pi-arrow-left" /> Documents
      </button>
      <div>
        <h1>Add a document</h1>
        <p>Select a document type and add its pages.</p>
      </div>
    </header>
    <div class="capture-layout">
      <div class="capture-steps">
        <section class="capture-step">
          <header class="step-heading">
            <span class="step-number">1</span>
            <div>
              <h2>Choose the document type</h2>
              <p>The document type determines what we extract and check.</p>
            </div>
          </header>
          <div
            class="workflow-options"
            role="radiogroup"
            aria-label="Document type"
          >
            <button
              v-for="target in targetOptions"
              :key="target.doctype"
              type="button"
              class="workflow-option"
              :class="{ selected: selectedTarget === target.doctype }"
              role="radio"
              :aria-checked="selectedTarget === target.doctype"
              :disabled="busy"
              @click="selectTarget(target.doctype)"
            >
              <i :class="target.icon" /><span
                ><strong>{{ target.label }}</strong
                ><small>{{ target.description }}</small></span
              ><span class="radio-indicator"
                ><i
                  v-if="selectedTarget === target.doctype"
                  class="pi pi-check"
              /></span>
            </button>
          </div>
          <div v-if="selectedTarget" class="layout-select-field">
            <label for="scanner-layout">Page layout</label
            ><Select
              input-id="scanner-layout"
              v-model="selectedLayout"
              :disabled="busy"
              :options="visibleLayouts"
              option-label="layout"
              option-value="layout"
              placeholder="Choose a page layout"
              filter
              fluid
            /><small v-if="selectedProfile"
              >{{ selectedProfile.section_count }} configured sections · Pages
              are matched to this layout.</small
            >
          </div>
          <Message
            v-if="!layoutOptions.length"
            severity="warn"
            :closable="false"
            >No configured workflows are available for your account.</Message
          >
        </section>
        <section class="capture-step pages-step">
          <header class="step-heading">
            <span class="step-number">2</span>
            <div>
              <h2>Add the pages</h2>
              <p>
                Keep pages in order. You can preview, rotate or rearrange them.
              </p>
            </div>
            <span v-if="pages.length" class="page-tally"
              >{{ pages.length }} / {{ limits.max_pages }}</span
            >
          </header>
          <div
            class="pages-drop-surface"
            :class="{ dragging, 'has-pages': pages.length }"
            @dragenter.prevent="dragging = true"
            @dragover.prevent="dragging = true"
            @dragleave.prevent="dragging = false"
            @drop.prevent="!busy && onDrop($event)"
          >
            <div v-if="!pages.length" class="dropzone-empty">
              <span class="dropzone-symbol"
                ><i class="pi pi-file-arrow-up"
              /></span>
              <h3>Drop your scans here</h3>
              <p>
                JPG, PNG, WebP or PDF · Up to {{ limits.max_file_mb }} MB per
                file
              </p>
              <Button
                label="Choose files"
                icon="pi pi-folder-open"
                outlined
                :disabled="busy"
                @click="uploadInput.click()"
              />
            </div>
            <div
              v-else
              class="page-queue"
              aria-label="Pages ready for extraction"
            >
              <article
                v-for="(page, index) in pages"
                :key="page.id"
                class="queued-page"
              >
                <button
                  class="page-preview-frame"
                  :aria-label="'Preview page ' + (index + 1)"
                  @click="previewPage = page"
                >
                  <div v-if="page.isPdf" class="pdf-preview">
                    <i class="pi pi-file-pdf" /><span>PDF document</span>
                  </div>
                  <img
                    v-else
                    :src="page.preview"
                    :alt="'Page ' + (index + 1)"
                    :style="{ transform: 'rotate(' + page.rotation + 'deg)' }"
                  />
                  <span class="page-number">{{ index + 1 }}</span
                  ><span class="preview-hint"
                    ><i class="pi pi-search-plus" /> Preview</span
                  >
                </button>
                <div class="queued-page-copy">
                  <strong :title="page.name">{{ page.name }}</strong
                  ><small>{{ formatBytes(page.size) }}</small>
                </div>
                <div class="page-actions">
                  <Button
                    icon="pi pi-arrow-left"
                    text
                    :disabled="busy || index === 0"
                    aria-label="Move earlier"
                    @click="move(index, -1)"
                  />
                  <Button
                    icon="pi pi-arrow-right"
                    text
                    :disabled="busy || index === pages.length - 1"
                    aria-label="Move later"
                    @click="move(index, 1)"
                  />
                  <Button
                    icon="pi pi-refresh"
                    text
                    :disabled="busy || page.isPdf"
                    aria-label="Rotate page"
                    @click="rotate(page)"
                  />
                  <Button
                    icon="pi pi-trash"
                    text
                    severity="danger"
                    :disabled="busy"
                    aria-label="Remove page"
                    @click="remove(index)"
                  />
                </div>
              </article>
              <button
                class="add-another-page"
                :disabled="busy"
                @click="uploadInput.click()"
              >
                <i class="pi pi-plus" /><span>Add pages</span>
              </button>
            </div>
            <input
              ref="uploadInput"
              class="hidden-input"
              type="file"
              accept="image/jpeg,image/png,image/webp,application/pdf"
              multiple
              :disabled="busy"
              @change="
                addFiles($event.target.files);
                $event.target.value = '';
              "
            />
            <input
              ref="cameraInput"
              class="hidden-input"
              type="file"
              accept="image/*"
              capture="environment"
              :disabled="busy"
              @change="
                addFiles($event.target.files);
                $event.target.value = '';
              "
            />
          </div>
          <div class="capture-source-actions">
            <Button
              label="Use camera"
              icon="pi pi-camera"
              text
              :disabled="busy"
              @click="openCamera"
            /><span>Your source document stays intact.</span>
          </div>
          <Message v-if="error" severity="error" :closable="false">{{
            error
          }}</Message>
          <div v-if="busy" class="upload-message" role="status">
            <div>
              <strong>{{ upload.label }}</strong
              ><span>{{ progress }}%</span>
            </div>
            <ProgressBar :value="progress" :show-value="false" />
          </div>
        </section>
      </div>
      <aside class="capture-brief">
        <h2>Phone scanner</h2>
        <p class="capture-help">
          Connect your phone to add scanned pages to this document.
        </p>
        <LiveScannerBridge
          ref="liveBridge"
          :layouts="layouts"
          :user="user"
          :site-name="siteName"
          @page="receiveLive"
        />
        <Message
          v-if="processing.provider && !processing.provider.ready"
          severity="warn"
          :closable="false"
          >{{
            processing.provider.reason ||
            "The extractor is not ready. Check the model configuration."
          }}</Message
        >
        <details class="phone-source">
          <summary>
            How processing works <i class="pi pi-chevron-down" />
          </summary>
          <p class="capture-help">
            The system reads your pages and checks the extracted details against
            existing records. You review the result before an entry is created.
            Nothing is submitted automatically.
          </p>
        </details>
      </aside>
    </div>
    <footer class="capture-submit">
      <div>
        <strong>{{
          selectedProfile?.target_label ||
          (selectedTarget ? "Select a page layout" : "Select a workflow")
        }}</strong
        ><span>{{
          pages.length
            ? pages.length +
              " page" +
              (pages.length === 1 ? "" : "s") +
              " ready"
            : "Add pages to continue"
        }}</span>
      </div>
      <Button
        label="Extract & verify"
        icon="pi pi-arrow-right"
        icon-pos="right"
        :loading="busy"
        :disabled="
          !pages.length ||
          (!selectedProfile && !phoneFolders.size) ||
          (processing.provider && !processing.provider.ready)
        "
        @click="start"
      />
    </footer>
    <Dialog
      v-model:visible="cameraOpen"
      modal
      header="Scan page"
      class="camera-dialog"
      @hide="closeCamera"
      ><video
        ref="cameraVideo"
        class="camera-preview"
        playsinline
        muted /><template #footer
        ><Button label="Cancel" text @click="closeCamera" /><Button
          label="Capture page"
          icon="pi pi-camera"
          @click="capturePhoto" /></template
    ></Dialog>
    <Dialog
      :visible="Boolean(previewPage)"
      modal
      header="Page preview"
      class="page-preview-dialog"
      @update:visible="
        (visible) => {
          if (!visible) previewPage = null;
        }
      "
    >
      <img
        v-if="previewPage && !previewPage.isPdf"
        :src="previewPage.preview"
        :alt="previewPage.name"
        class="large-page-preview"
        :style="{ transform: 'rotate(' + previewPage.rotation + 'deg)' }"
      />
      <div v-else-if="previewPage" class="pdf-preview large">
        <i class="pi pi-file-pdf" /><span>{{ previewPage.name }}</span>
        <p>PDF pages are rendered during extraction.</p>
      </div>
    </Dialog>
  </section>
</template>

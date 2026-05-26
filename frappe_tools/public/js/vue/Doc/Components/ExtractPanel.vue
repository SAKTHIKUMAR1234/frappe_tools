<template>
    <div class="ex-panel">
        <div class="ex-header">
            <div class="ex-header-left">
                <div class="ex-icon-wrap">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                        <polyline points="14 2 14 8 20 8"/>
                        <path d="M9 15l2 2 4-4"/>
                    </svg>
                </div>
                <div>
                    <h4 class="ex-title">Create {{ targetLabel }} from Scan</h4>
                    <p class="ex-subtitle">{{ stageLabel }}</p>
                </div>
            </div>
            <div class="ex-header-right">
                <button class="ex-btn ex-btn--ghost" @click="emit('exit')">Back to List</button>
            </div>
        </div>

        <!-- SCANNING -->
        <div v-if="stage === 'scanning'" class="ex-stage">
            <div class="ex-toolbar">
                <span class="ex-count">{{ images.length }} {{ images.length === 1 ? 'page' : 'pages' }} scanned</span>
                <div class="ex-actions">
                    <button class="ex-btn ex-btn--outline" :disabled="!isConnected"
                        :title="!isConnected ? 'Connect the mobile scanner first' : ''" @click="emit('triggerScan')">
                        Scan Page
                    </button>
                    <button class="ex-btn ex-btn--primary" :disabled="images.length === 0" @click="startExtraction">
                        Extract &amp; Review
                    </button>
                </div>
            </div>

            <div v-if="images.length" class="ex-thumbs">
                <div v-for="(img, i) in images" :key="i" class="ex-thumb">
                    <img :src="img" alt="" />
                    <button class="ex-thumb-x" @click="removeImage(i)" title="Remove">&times;</button>
                    <span class="ex-thumb-no">{{ i + 1 }}</span>
                </div>
            </div>
            <div v-else class="ex-hint">
                <p>Connect your mobile scanner with the PIN above, then scan the document pages. They will appear here, then click <strong>Extract &amp; Review</strong>.</p>
            </div>
        </div>

        <!-- EXTRACTING -->
        <div v-else-if="stage === 'extracting'" class="ex-loading">
            <div class="ex-spinner"></div>
            <h5>Reading your document…</h5>
            <p>Sending {{ images.length }} {{ images.length === 1 ? 'page' : 'pages' }} to the model with the {{ targetLabel }} rule books. This can take a moment.</p>
        </div>

        <!-- REVIEW -->
        <ExtractReviewPanel v-else-if="stage === 'review'" :extraction="extractionName" @created="onCreated" />

        <!-- VERIFY: the real document beside the scan, two-way highlight -->
        <VerifySplitView v-else-if="stage === 'verify' && createdDoc" :extraction="extractionName"
            :doctype="props.target_doctype" :docname="createdDoc" @exit="emit('exit')" />

        <!-- CREATED (fallback) -->
        <div v-else-if="stage === 'created'" class="ex-done">
            <div class="ex-done-check">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="20 6 9 17 4 12"/>
                </svg>
            </div>
            <h5>{{ targetLabel }} created</h5>
            <p class="ex-done-name">{{ createdDoc }}</p>
            <div class="ex-actions">
                <button class="ex-btn ex-btn--primary" @click="openCreated">Open Document</button>
                <button class="ex-btn ex-btn--outline" @click="stage = 'review'">View Provenance</button>
                <button class="ex-btn ex-btn--ghost" @click="emit('exit')">Back to List</button>
            </div>
        </div>

        <div v-if="error" class="ex-error">{{ error }}</div>
    </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useGloabalDragMemory } from '../../Store/doc_scanner_drag_drop_memory'
import { useSessionStore } from '../../Store/docscanner_store'
import ExtractReviewPanel from '../Pages/ExtractReviewPanel.vue'
import VerifySplitView from '../Pages/VerifySplitView.vue'

const props = defineProps({
    target_doctype: { type: String, required: true },
    extraction: { type: String, default: null },
})
const emit = defineEmits(['triggerScan', 'exit'])

const dragStore = useGloabalDragMemory()
const sessionStore = useSessionStore()

const stage = ref('scanning')          // scanning | extracting | review | created
const extractionName = ref(props.extraction || null)
const createdDoc = ref(null)
const error = ref(null)

const images = computed(() => dragStore.imagesList)
const isConnected = computed(() => sessionStore.status === 'connected')
const targetLabel = computed(() => __(props.target_doctype))
const stageLabel = computed(() => ({
    scanning: 'Scan the document pages',
    extracting: 'Extracting data…',
    review: 'Review the extracted data, then create',
    created: 'Document created',
}[stage.value] || ''))

function removeImage(i) {
    dragStore.setImagesDetails(images.value.filter((_, idx) => idx !== i))
}

async function startExtraction() {
    error.value = null
    try {
        const r = await frappe.call({
            method: 'frappe_tools.api.doc_extract.extract_document',
            args: { target_doctype: props.target_doctype, images: JSON.stringify(images.value) },
        })
        extractionName.value = r.message.extraction
        stage.value = 'extracting'
    } catch (e) {
        error.value = __('Could not start extraction. Check Document Extraction Settings.')
    }
}

const realtimeHandler = (data) => {
    if (!data || data.extraction !== extractionName.value) return
    if (data.status === 'Review') {
        stage.value = 'review'
    } else if (data.status === 'Failed') {
        error.value = data.error || __('Extraction failed. Please try again.')
        stage.value = 'scanning'
    }
}

function onCreated(payload) {
    createdDoc.value = payload && payload.docname
    stage.value = createdDoc.value ? 'verify' : 'created'
    dragStore.clearAll()
}

function openCreated() {
    if (createdDoc.value) frappe.set_route('Form', props.target_doctype, createdDoc.value)
}

onMounted(async () => {
    frappe.realtime.on('frappe_tools_extraction', realtimeHandler)

    if (props.extraction) {
        try {
            const r = await frappe.call({
                method: 'frappe_tools.api.doc_extract.get_extraction',
                args: { extraction: props.extraction },
            })
            const st = r.message.status
            if (st === 'Created') {
                createdDoc.value = r.message.created_document
                stage.value = createdDoc.value ? 'verify' : 'created'
            } else if (st === 'Extracting') {
                stage.value = 'extracting'
            } else {
                stage.value = 'review'
            }
        } catch (e) {
            error.value = __('Could not load the extraction.')
        }
    }
})

onBeforeUnmount(() => {
    frappe.realtime.off('frappe_tools_extraction', realtimeHandler)
})
</script>

<style scoped>
.ex-panel {
    width: 100%;
    max-width: 1200px;
    margin: 0 auto;
}

.ex-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px;
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    margin-bottom: 16px;
}

.ex-header-left {
    display: flex;
    align-items: center;
    gap: 12px;
}

.ex-icon-wrap {
    width: 38px;
    height: 38px;
    border-radius: 10px;
    background: #ecfdf5;
    color: #059669;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
}

.ex-title {
    margin: 0;
    font-size: 15px;
    font-weight: 650;
    color: #1e293b;
    line-height: 1.3;
}

.ex-subtitle {
    margin: 0;
    font-size: 12px;
    color: #94a3b8;
    line-height: 1.3;
}

.ex-stage {
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 16px 20px;
}

.ex-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
}

.ex-count {
    font-size: 13px;
    font-weight: 500;
    color: #64748b;
}

.ex-actions {
    display: flex;
    gap: 8px;
}

.ex-thumbs {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 12px;
}

.ex-thumb {
    position: relative;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    overflow: hidden;
    background: #0f172a;
    aspect-ratio: 3 / 4;
}

.ex-thumb img {
    width: 100%;
    height: 100%;
    object-fit: contain;
}

.ex-thumb-x {
    position: absolute;
    top: 4px;
    right: 4px;
    width: 22px;
    height: 22px;
    border: none;
    border-radius: 50%;
    background: rgba(15, 23, 42, 0.7);
    color: #fff;
    font-size: 15px;
    line-height: 1;
    cursor: pointer;
}

.ex-thumb-no {
    position: absolute;
    bottom: 4px;
    left: 4px;
    background: rgba(15, 23, 42, 0.7);
    color: #fff;
    font-size: 11px;
    padding: 1px 7px;
    border-radius: 10px;
}

.ex-hint {
    text-align: center;
    padding: 40px 20px;
    color: #94a3b8;
    font-size: 13px;
    line-height: 1.6;
}

.ex-loading,
.ex-done {
    text-align: center;
    padding: 56px 24px;
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
}

.ex-loading h5,
.ex-done h5 {
    margin: 16px 0 6px;
    font-size: 16px;
    font-weight: 650;
    color: #1e293b;
}

.ex-loading p,
.ex-done p {
    margin: 0 auto;
    max-width: 420px;
    font-size: 13px;
    color: #94a3b8;
    line-height: 1.6;
}

.ex-spinner {
    width: 32px;
    height: 32px;
    border: 3px solid #e2e8f0;
    border-top-color: #059669;
    border-radius: 50%;
    animation: ex-spin 0.7s linear infinite;
    margin: 0 auto;
}

@keyframes ex-spin {
    to { transform: rotate(360deg); }
}

.ex-done-check {
    width: 56px;
    height: 56px;
    border-radius: 50%;
    background: #ecfdf5;
    color: #059669;
    display: flex;
    align-items: center;
    justify-content: center;
    margin: 0 auto;
}

.ex-done-name {
    font-weight: 600 !important;
    color: #1e293b !important;
    margin-bottom: 18px !important;
}

.ex-done .ex-actions {
    justify-content: center;
    margin-top: 18px;
}

.ex-error {
    margin-top: 12px;
    padding: 10px 14px;
    background: #fef2f2;
    border: 1px solid #fecaca;
    color: #b91c1c;
    border-radius: 8px;
    font-size: 13px;
}

.ex-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 32px;
    padding: 0 16px;
    font-size: 12.5px;
    font-weight: 600;
    border-radius: 6px;
    border: none;
    cursor: pointer;
    transition: all 0.15s ease;
    white-space: nowrap;
}

.ex-btn:disabled {
    opacity: 0.45;
    cursor: not-allowed;
}

.ex-btn--primary {
    background: #059669;
    color: #fff;
}

.ex-btn--primary:hover:not(:disabled) {
    background: #047857;
}

.ex-btn--outline {
    background: transparent;
    color: #059669;
    border: 1px solid #a7f3d0;
}

.ex-btn--outline:hover:not(:disabled) {
    background: #ecfdf5;
}

.ex-btn--ghost {
    background: transparent;
    color: #64748b;
}

.ex-btn--ghost:hover {
    background: #f1f5f9;
    color: #334155;
}
</style>

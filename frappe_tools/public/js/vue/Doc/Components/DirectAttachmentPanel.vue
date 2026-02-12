<template>
    <div class="da-panel">
        <!-- Header -->
        <div class="da-header">
            <div class="da-header-left">
                <div class="da-icon-wrap">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                    </svg>
                </div>
                <div>
                    <h4 class="da-title">Direct Attachment</h4>
                    <p class="da-subtitle">{{ doctype }} &middot; {{ docname }}</p>
                </div>
            </div>
            <div class="da-header-right">
                <span class="da-field-count" v-if="!loading && attachFields.length">
                    {{ attachFields.length }} {{ attachFields.length === 1 ? 'field' : 'fields' }}
                </span>
                <button class="da-btn da-btn--outline" @click="emit('changeDoc')">
                    Change Document
                </button>
            </div>
        </div>

        <!-- Loading -->
        <div v-if="loading" class="da-loading">
            <div class="da-spinner"></div>
            <span>Loading attachment fields...</span>
        </div>

        <!-- Empty State -->
        <div v-else-if="attachFields.length === 0" class="da-empty">
            <div class="da-empty-icon">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                    <circle cx="8.5" cy="8.5" r="1.5"/>
                    <polyline points="21 15 16 10 5 21"/>
                </svg>
            </div>
            <h5>No attachment fields found</h5>
            <p>This DocType doesn't have any Attach or Attach Image fields.<br/>Try using <strong>Template Scanning</strong> mode instead.</p>
        </div>

        <!-- Fields Grid -->
        <div v-else class="da-grid">
            <div
                v-for="field in attachFields"
                :key="field.fieldname"
                class="da-card"
                :class="{
                    'da-card--selected': selectedField === field.fieldname,
                    'da-card--uploading': uploadingField === field.fieldname,
                    'da-card--has-image': fieldPreviews[field.fieldname]
                }"
            >
                <!-- Thumbnail Area -->
                <div class="da-card-thumb" @click="!uploadingField && !selectedField && selectField(field.fieldname)">
                    <img
                        v-if="fieldPreviews[field.fieldname]"
                        :src="fieldPreviews[field.fieldname]"
                        alt=""
                    />
                    <div v-else class="da-card-empty">
                        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                            <circle cx="8.5" cy="8.5" r="1.5"/>
                            <polyline points="21 15 16 10 5 21"/>
                        </svg>
                    </div>

                    <!-- Upload overlay -->
                    <div v-if="uploadingField === field.fieldname" class="da-card-overlay da-card-overlay--upload">
                        <div class="da-spinner da-spinner--white"></div>
                        <span>Uploading...</span>
                    </div>

                    <!-- Waiting overlay -->
                    <div v-if="selectedField === field.fieldname" class="da-card-overlay da-card-overlay--waiting">
                        <div class="da-scan-pulse"></div>
                        <span>Waiting for scan...</span>
                    </div>

                    <!-- Success check (brief flash) -->
                    <div v-if="successField === field.fieldname" class="da-card-overlay da-card-overlay--success">
                        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="20 6 9 17 4 12"/>
                        </svg>
                    </div>
                </div>

                <!-- Card Footer -->
                <div class="da-card-footer">
                    <div class="da-card-info">
                        <span class="da-card-label">{{ field.label }}</span>
                        <span class="da-card-type">{{ field.fieldtype === 'Attach Image' ? 'Image' : 'File' }}</span>
                    </div>
                    <div class="da-card-actions">
                        <button
                            v-if="selectedField === field.fieldname"
                            class="da-btn da-btn--ghost"
                            @click="cancelSelection"
                        >
                            Cancel
                        </button>
                        <button
                            v-else-if="uploadingField !== field.fieldname"
                            class="da-btn"
                            :class="fieldPreviews[field.fieldname] ? 'da-btn--outline' : 'da-btn--primary'"
                            @click="selectField(field.fieldname)"
                            :disabled="!isConnected || !!selectedField || !!uploadingField"
                            :title="!isConnected ? 'Connect the mobile scanner first' : ''"
                        >
                            {{ fieldPreviews[field.fieldname] ? 'Replace' : 'Scan' }}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>
</template>

<script setup>
import { ref, reactive, watch, computed, onMounted } from 'vue'
import { useGloabalDragMemory } from '../../Store/doc_scanner_drag_drop_memory'
import { useSessionStore } from '../../Store/docscanner_store'

const props = defineProps({
    doctype: { type: String, required: true },
    docname: { type: String, required: true },
})

const emit = defineEmits(['changeDoc', 'triggerScan'])

const dragStore = useGloabalDragMemory()
const sessionStore = useSessionStore()

const isConnected = computed(() => sessionStore.status === 'connected')

const attachFields = ref([])
const selectedField = ref(null)
const uploadingField = ref(null)
const successField = ref(null)
const loading = ref(true)
const lastProcessedLength = ref(0)
const fieldPreviews = reactive({})

async function fetchFields() {
    loading.value = true
    try {
        const r = await frappe.call({
            method: 'frappe_tools.api.doc_scanner.get_attach_fields',
            args: { doctype: props.doctype, docname: props.docname },
        })
        attachFields.value = r.message || []
        for (const field of attachFields.value) {
            if (field.currentValue) {
                fieldPreviews[field.fieldname] = field.currentValue
            }
        }
    } catch (err) {
        frappe.msgprint(__('Failed to load attachment fields'))
    } finally {
        loading.value = false
    }
}

function selectField(fieldname) {
    if (fieldPreviews[fieldname]) {
        frappe.confirm(
            __('The current file attached to this field will be permanently deleted and replaced. Continue?'),
            () => {
                selectedField.value = fieldname
                lastProcessedLength.value = dragStore.imagesList.length
                emit('triggerScan')
            }
        )
        return
    }
    selectedField.value = fieldname
    lastProcessedLength.value = dragStore.imagesList.length
    emit('triggerScan')
}

function cancelSelection() {
    selectedField.value = null
}

function flashSuccess(fieldname) {
    successField.value = fieldname
    setTimeout(() => {
        successField.value = null
    }, 1200)
}

watch(
    () => dragStore.imagesList.length,
    async (newLen) => {
        if (!selectedField.value) return
        if (uploadingField.value) return
        if (newLen <= lastProcessedLength.value) return

        const imageData = dragStore.imagesList[dragStore.imagesList.length - 1]
        dragStore.imagesList.splice(dragStore.imagesList.length - 1, 1)

        const fieldname = selectedField.value
        uploadingField.value = fieldname
        selectedField.value = null

        try {
            const r = await frappe.call({
                method: 'frappe_tools.api.doc_scanner.attach_image_to_field',
                args: {
                    doctype: props.doctype,
                    docname: props.docname,
                    fieldname: fieldname,
                    image_data: imageData,
                },
            })
            fieldPreviews[fieldname] = r.message.file_url
            flashSuccess(fieldname)
            frappe.show_alert(
                { message: __('Image attached successfully'), indicator: 'green' },
                5
            )
        } catch (err) {
            frappe.show_alert(
                { message: __('Failed to attach image'), indicator: 'red' },
                7
            )
        } finally {
            uploadingField.value = null
            lastProcessedLength.value = dragStore.imagesList.length
        }
    }
)

onMounted(() => {
    lastProcessedLength.value = dragStore.imagesList.length
    fetchFields()
})
</script>

<style scoped>
/* ── Panel ── */
.da-panel {
    width: 100%;
    max-width: 960px;
    margin: 0 auto;
}

/* ── Header ── */
.da-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px;
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    margin-bottom: 20px;
}

.da-header-left {
    display: flex;
    align-items: center;
    gap: 12px;
}

.da-icon-wrap {
    width: 38px;
    height: 38px;
    border-radius: 10px;
    background: #eef2ff;
    color: #4f46e5;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
}

.da-title {
    margin: 0;
    font-size: 15px;
    font-weight: 650;
    color: #1e293b;
    line-height: 1.3;
}

.da-subtitle {
    margin: 0;
    font-size: 12px;
    color: #94a3b8;
    line-height: 1.3;
}

.da-header-right {
    display: flex;
    align-items: center;
    gap: 10px;
}

.da-field-count {
    font-size: 12px;
    font-weight: 500;
    color: #64748b;
    background: #f1f5f9;
    padding: 4px 10px;
    border-radius: 20px;
}

/* ── Loading ── */
.da-loading {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    padding: 60px 20px;
    color: #94a3b8;
    font-size: 13px;
}

.da-spinner {
    width: 24px;
    height: 24px;
    border: 2.5px solid #e2e8f0;
    border-top-color: #4f46e5;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
}

.da-spinner--white {
    border-color: rgba(255,255,255,0.3);
    border-top-color: #fff;
}

@keyframes spin {
    to { transform: rotate(360deg); }
}

/* ── Empty State ── */
.da-empty {
    text-align: center;
    padding: 60px 20px;
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
}

.da-empty-icon {
    color: #cbd5e1;
    margin-bottom: 16px;
}

.da-empty h5 {
    margin: 0 0 8px;
    font-size: 15px;
    font-weight: 600;
    color: #334155;
}

.da-empty p {
    margin: 0;
    font-size: 13px;
    color: #94a3b8;
    line-height: 1.6;
}

/* ── Grid ── */
.da-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 16px;
}

/* ── Card ── */
.da-card {
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    overflow: hidden;
    transition: all 0.2s ease;
}

.da-card:hover {
    border-color: #cbd5e1;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.03);
}

.da-card--selected {
    border-color: #4f46e5;
    box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.1);
}

.da-card--selected:hover {
    border-color: #4f46e5;
}

.da-card--uploading {
    border-color: #e2e8f0;
}

/* ── Thumbnail ── */
.da-card-thumb {
    position: relative;
    width: 100%;
    height: 180px;
    background: #f8fafc;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    overflow: hidden;
}

.da-card--has-image .da-card-thumb {
    background: #0f172a;
}

.da-card-thumb img {
    width: 100%;
    height: 100%;
    object-fit: contain;
}

.da-card-empty {
    color: #cbd5e1;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
}

/* ── Overlays ── */
.da-card-overlay {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    font-size: 13px;
    font-weight: 500;
}

.da-card-overlay--upload {
    background: rgba(15, 23, 42, 0.75);
    color: #fff;
    backdrop-filter: blur(2px);
}

.da-card-overlay--waiting {
    background: rgba(79, 70, 229, 0.08);
    color: #4f46e5;
    border: 2px dashed #4f46e5;
    border-radius: 0;
}

.da-card-overlay--success {
    background: rgba(16, 185, 129, 0.12);
    color: #059669;
    animation: fadeOut 1.2s ease forwards;
}

@keyframes fadeOut {
    0%, 60% { opacity: 1; }
    100% { opacity: 0; }
}

/* ── Scan Pulse ── */
.da-scan-pulse {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: rgba(79, 70, 229, 0.15);
    position: relative;
}

.da-scan-pulse::before {
    content: '';
    position: absolute;
    inset: -6px;
    border-radius: 50%;
    border: 2px solid rgba(79, 70, 229, 0.3);
    animation: pulse-ring 1.5s ease-out infinite;
}

@keyframes pulse-ring {
    0% { transform: scale(0.8); opacity: 1; }
    100% { transform: scale(1.6); opacity: 0; }
}

/* ── Card Footer ── */
.da-card-footer {
    padding: 10px 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-top: 1px solid #f1f5f9;
}

.da-card-info {
    display: flex;
    flex-direction: column;
    gap: 1px;
    min-width: 0;
}

.da-card-label {
    font-size: 13px;
    font-weight: 600;
    color: #1e293b;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.da-card-type {
    font-size: 11px;
    color: #94a3b8;
}

/* ── Buttons ── */
.da-card-actions {
    flex-shrink: 0;
}

.da-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 30px;
    padding: 0 14px;
    font-size: 12px;
    font-weight: 600;
    border-radius: 6px;
    border: none;
    cursor: pointer;
    transition: all 0.15s ease;
    white-space: nowrap;
}

.da-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
}

.da-btn--primary {
    background: #4f46e5;
    color: #fff;
}

.da-btn--primary:hover:not(:disabled) {
    background: #4338ca;
}

.da-btn--outline {
    background: transparent;
    color: #4f46e5;
    border: 1px solid #c7d2fe;
}

.da-btn--outline:hover:not(:disabled) {
    background: #eef2ff;
}

.da-btn--ghost {
    background: transparent;
    color: #64748b;
}

.da-btn--ghost:hover {
    background: #f1f5f9;
    color: #334155;
}
</style>

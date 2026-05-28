<template>
    <div class="vf-root">
        <div v-if="loading" class="vf-loading"><div class="vf-spinner"></div><span>Loading…</span></div>

        <template v-else>
            <div class="vf-bar">
                <div class="vf-bar-left">
                    <span class="vf-title">Verify against scan</span>
                    <span class="vf-sep">&middot;</span>
                    <span class="vf-muted">{{ targetDoctype }} {{ docname }}</span>
                </div>
                <div class="vf-bar-right">
                    <button class="vf-btn vf-btn--outline" @click="openForm">Open Full Form</button>
                    <button class="vf-btn vf-btn--ghost" @click="emit('exit')">Done</button>
                </div>
            </div>

            <div class="vf-grid">
                <!-- LEFT: scanned doc + provenance overlay -->
                <div class="vf-doc">
                    <div v-if="pages.length > 1" class="vf-pagetabs">
                        <button v-for="p in pages" :key="p.page_no" class="vf-pagetab"
                            :class="{ 'vf-pagetab--active': p.page_no === activePage }" @click="activePage = p.page_no">
                            Page {{ p.page_no }}
                        </button>
                    </div>
                    <div v-if="currentPage" class="vf-img-wrap">
                        <img :src="currentPage.image" alt="" class="vf-img" />
                        <svg class="vf-overlay" viewBox="0 0 100 100" preserveAspectRatio="none">
                            <rect v-for="b in boxesOnPage" :key="b.key" class="vf-box"
                                :class="{ 'vf-box--active': b.key === selectedKey, ['vf-box--' + b.kind]: true }"
                                :x="b.bbox.x * 100" :y="b.bbox.y * 100" :width="b.bbox.w * 100" :height="b.bbox.h * 100"
                                rx="0.4" @click="selectKey(b.key)">
                            </rect>
                        </svg>
                    </div>
                    <div v-else class="vf-noimg">No page image.</div>
                </div>

                <!-- RIGHT: the real document form (embedded), with graceful fallback -->
                <div class="vf-form-pane">
                    <div ref="formHost" class="vf-form-host"></div>
                    <div v-if="formError" class="vf-form-fallback">
                        <p>Couldn't embed the live form here.</p>
                        <button class="vf-btn vf-btn--primary" @click="openForm">Open {{ targetDoctype }} {{ docname }}</button>
                    </div>
                </div>
            </div>
        </template>
    </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
    extraction: { type: String, required: true },
    doctype: { type: String, required: true },
    docname: { type: String, required: true },
})
const emit = defineEmits(['exit'])

const loading = ref(true)
const pages = ref([])
const provenance = ref({ fields: {}, tables: {} })
const activePage = ref(1)
const selectedKey = ref(null)
const formError = ref(false)
const formHost = ref(null)
const targetDoctype = computed(() => props.doctype)

let frm = null

const currentPage = computed(() => pages.value.find(p => p.page_no === activePage.value) || pages.value[0])

// Build overlay boxes for the active page from the provenance map.
const boxesOnPage = computed(() => {
    const pageNo = currentPage.value && currentPage.value.page_no
    const out = []
    Object.entries(provenance.value.fields || {}).forEach(([fieldname, p]) => {
        if (p && p.bbox && (p.page || 1) === pageNo) out.push({ key: 'f:' + fieldname, kind: 'field', fieldname, bbox: p.bbox })
    })
    Object.entries(provenance.value.tables || {}).forEach(([table, rows]) => {
        Object.entries(rows || {}).forEach(([rowNo, p]) => {
            if (p && p.bbox && (p.page || 1) === pageNo) out.push({ key: 'l:' + table + ':' + rowNo, kind: 'line', table, bbox: p.bbox })
        })
    })
    return out
})

function selectKey(key) {
    selectedKey.value = key
    if (!frm) return
    try {
        if (key.startsWith('f:')) {
            frm.scroll_to_field(key.slice(2))
        } else if (key.startsWith('l:')) {
            const table = key.split(':')[1]
            if (frm.fields_dict[table]) frm.scroll_to_field(table)
        }
    } catch (e) { /* non-fatal */ }
}

function highlightField(fieldname) {
    const key = 'f:' + fieldname
    const box = boxesOnPage.value.find(b => b.key === key)
        || Object.entries(provenance.value.fields || {}).find(([fn]) => fn === fieldname)
    if (provenance.value.fields[fieldname]) {
        activePage.value = provenance.value.fields[fieldname].page || activePage.value
    }
    selectedKey.value = key
}

function openForm() {
    frappe.set_route('Form', props.doctype, props.docname)
}

function renderForm() {
    try {
        frappe.model.with_doctype(props.doctype, () => {
            frappe.model.with_doc(props.doctype, props.docname, () => {
                const host = formHost.value
                if (!host) { formError.value = true; return }
                $(host).empty()
                frm = new frappe.ui.form.Form(props.doctype, $(host), true, null)
                frm.refresh(props.docname)
                // Field → region: highlight the source box when a field is focused.
                setTimeout(() => wireFieldFocus(), 600)
            })
        })
    } catch (e) {
        formError.value = true
    }
}

function wireFieldFocus() {
    if (!frm || !frm.fields_dict) return
    Object.keys(provenance.value.fields || {}).forEach(fieldname => {
        const field = frm.fields_dict[fieldname]
        if (field && field.$input) {
            field.$input.on('focus click', () => highlightField(fieldname))
        }
    })
}

async function load() {
    loading.value = true
    try {
        const r = await frappe.call({ method: 'frappe_tools.api.doc_extract.get_extraction', args: { extraction: props.extraction } })
        const m = r.message || {}
        pages.value = m.pages || []
        provenance.value = m.provenance || { fields: {}, tables: {} }
        activePage.value = (pages.value[0] && pages.value[0].page_no) || 1
    } catch (e) {
        frappe.msgprint(__('Failed to load provenance.'))
    } finally {
        loading.value = false
    }
    // Render the form after the DOM (formHost) exists.
    setTimeout(renderForm, 50)
}

onMounted(load)
onBeforeUnmount(() => {
    try { if (frm && frm.$wrapper) frm.$wrapper.remove() } catch (e) { /* ignore */ }
    frm = null
})
</script>

<style scoped>
.vf-root { width: 100%; max-width: 1300px; margin: 0 auto; }
.vf-loading { display: flex; align-items: center; justify-content: center; gap: 12px; padding: 60px; color: #94a3b8; font-size: 13px; }
.vf-spinner { width: 22px; height: 22px; border: 2.5px solid #e2e8f0; border-top-color: #4f46e5; border-radius: 50%; animation: vf-spin .7s linear infinite; }
@keyframes vf-spin { to { transform: rotate(360deg); } }

.vf-bar { display: flex; align-items: center; justify-content: space-between; padding: 10px 16px; background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; margin-bottom: 12px; }
.vf-bar-left { display: flex; align-items: center; gap: 8px; }
.vf-title { font-size: 13px; font-weight: 650; color: #1e293b; }
.vf-sep { color: #cbd5e1; }
.vf-muted { font-size: 12.5px; color: #64748b; }
.vf-bar-right { display: flex; gap: 8px; }

.vf-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; align-items: start; }

.vf-doc { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px; position: sticky; top: 70px; }
.vf-pagetabs { display: flex; gap: 6px; margin-bottom: 10px; flex-wrap: wrap; }
.vf-pagetab { border: 1px solid #e2e8f0; background: #fff; color: #475569; font-size: 12px; font-weight: 600; padding: 4px 12px; border-radius: 6px; cursor: pointer; }
.vf-pagetab--active { background: #4f46e5; border-color: #4f46e5; color: #fff; }
.vf-img-wrap { position: relative; line-height: 0; border-radius: 8px; overflow: hidden; background: #0f172a; }
.vf-img { width: 100%; height: auto; display: block; }
.vf-overlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; }
.vf-box { fill: rgba(0,0,0,0); pointer-events: all; cursor: pointer; vector-effect: non-scaling-stroke; stroke-width: 1; stroke-opacity: .7; }
.vf-box--field { stroke: #d97706; }
.vf-box--line { stroke: #0d9488; }
.vf-box--active { stroke: #4f46e5 !important; stroke-width: 2; stroke-opacity: 1; fill: rgba(79,70,229,.12); }

.vf-noimg { padding: 40px; text-align: center; color: #94a3b8; font-size: 13px; }

.vf-form-pane { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; min-height: 300px; overflow: hidden; }
/* Hide the embedded form's page toolbar/header so only the fields show. */
.vf-form-host :deep(.page-head),
.vf-form-host :deep(.page-actions),
.vf-form-host :deep(.form-message),
.vf-form-host :deep(.layout-side-section) { display: none !important; }
.vf-form-host :deep(.page-body) { padding: 8px 12px; }
.vf-form-fallback { padding: 40px 24px; text-align: center; color: #64748b; font-size: 13px; }

.vf-btn { display: inline-flex; align-items: center; height: 30px; padding: 0 14px; font-size: 12px; font-weight: 600; border-radius: 6px; border: 1px solid #e2e8f0; background: #fff; color: #475569; cursor: pointer; }
.vf-btn--primary { background: #4f46e5; border-color: #4f46e5; color: #fff; }
.vf-btn--outline { color: #4f46e5; border-color: #c7d2fe; }
.vf-btn--ghost { border-color: transparent; }
</style>

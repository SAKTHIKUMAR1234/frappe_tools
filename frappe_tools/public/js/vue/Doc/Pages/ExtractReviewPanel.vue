<template>
    <div class="rv-root">
        <div v-if="loading" class="rv-loading">
            <div class="rv-spinner"></div>
            <span>Loading extracted data…</span>
        </div>

        <template v-else>
            <div class="rv-bar">
                <div class="rv-bar-left">
                    <span class="rv-bar-title">Review &amp; Verify</span>
                    <span class="rv-bar-sep">&middot;</span>
                    <span class="rv-bar-muted">{{ targetLabel }}</span>
                    <span v-if="lines.length" class="rv-bar-sep">&middot;</span>
                    <span v-if="lines.length" class="rv-bar-muted">{{ resolvedLines }}/{{ lines.length }} lines resolved</span>
                    <span v-if="modelUsed" class="rv-bar-sep">&middot;</span>
                    <span v-if="modelUsed" class="rv-bar-muted">{{ modelUsed }}</span>
                </div>
                <div class="rv-bar-right">
                    <span class="rv-summary" :class="{ 'rv-summary--warn': unresolvedLines }">
                        <template v-if="unresolvedLines">{{ unresolvedLines }} line(s) need attention</template>
                        <template v-else>Ready to create</template>
                    </span>
                </div>
            </div>

            <div class="rv-grid">
                <!-- LEFT: scanned page + provenance overlays -->
                <div class="rv-doc">
                    <div v-if="pages.length > 1" class="rv-pagetabs">
                        <button v-for="p in pages" :key="p.page_no" class="rv-pagetab"
                            :class="{ 'rv-pagetab--active': p.page_no === activePage }"
                            @click="activePage = p.page_no">Page {{ p.page_no }}</button>
                    </div>
                    <div v-if="currentPage" class="rv-img-wrap">
                        <img :src="currentPage.image" alt="" class="rv-img" />
                        <div v-for="box in boxesOnPage" :key="box.key" class="rv-box"
                            :class="{ 'rv-box--active': box.key === selectedKey, ['rv-box--' + box.kind]: true }"
                            :style="boxStyle(box.bbox)" :title="box.label" @click="selectBox(box)">
                            <span class="rv-box-tag">{{ box.label }}</span>
                        </div>
                    </div>
                    <div v-else class="rv-noimg">No page image.</div>
                </div>

                <!-- RIGHT: header fields + line items -->
                <div class="rv-side">
                    <div class="rv-section-title">Header</div>
                    <div v-for="field in fields" :key="field.name" class="rv-field"
                        :class="{ 'rv-field--active': selectedKey === 'f:' + field.fieldname, 'rv-field--rejected': field.status === 'Rejected' }">
                        <div class="rv-field-head" @click="selectField(field)">
                            <span class="rv-field-label">{{ field.label || field.fieldname }}<span v-if="field.required" class="rv-req">*</span></span>
                            <span class="rv-conf" :class="'rv-conf--' + confClass(field.confidence)">{{ pct(field.confidence) }}</span>
                        </div>

                        <!-- Link field (e.g. Supplier) → resolver -->
                        <div v-if="field.link_doctype" class="rv-resolver">
                            <div class="rv-resolved" v-if="field.value">
                                <span class="rv-chip rv-chip--ok">{{ field.value }}</span>
                                <span v-if="field.match_method" class="rv-method">{{ field.match_method }}</span>
                            </div>
                            <div v-else class="rv-unresolved">Unresolved — pick or create</div>
                            <div class="rv-cands" v-if="field.candidates && field.candidates.length">
                                <button v-for="c in field.candidates.slice(0, 4)" :key="c.value" class="rv-cand"
                                    :class="{ 'rv-cand--on': c.value === field.value }" @click="pickLink(field, c.value)"
                                    :title="c.method + ' · ' + pct(c.score)">{{ c.label }} <em>{{ pct(c.score) }}</em></button>
                            </div>
                            <div class="rv-actions-row">
                                <button class="rv-mini" @click="searchLink(field)">Search…</button>
                                <button v-if="field.fieldname === 'supplier'" class="rv-mini" @click="newSupplier(field)">New Supplier</button>
                                <span v-if="field.llm_value" class="rv-printed">printed: "{{ field.llm_value }}"</span>
                            </div>
                        </div>

                        <!-- Plain editable field -->
                        <div v-else class="rv-field-body">
                            <label v-if="field.fieldtype === 'Check'" class="rv-check">
                                <input type="checkbox" :checked="isChecked(field)" @change="onCheck(field, $event)" /> {{ isChecked(field) ? 'Yes' : 'No' }}
                            </label>
                            <select v-else-if="field.fieldtype === 'Select' && field.options" class="rv-input" v-model="field.value" @change="persist(field)">
                                <option value=""></option>
                                <option v-for="opt in field.options" :key="opt" :value="opt">{{ opt }}</option>
                            </select>
                            <input v-else-if="field.fieldtype === 'Date'" type="date" class="rv-input" v-model="field.value" @change="persist(field)" />
                            <input v-else type="text" class="rv-input" v-model="field.value" @change="persist(field)" />
                            <p v-if="field.llm_raw_text" class="rv-raw"><span class="rv-raw-label">on document:</span> "{{ field.llm_raw_text }}"</p>
                        </div>
                    </div>

                    <!-- LINE ITEMS -->
                    <template v-if="lines.length">
                        <div class="rv-section-title">Line Items</div>
                        <div v-for="line in lines" :key="line.row_no" class="rv-line"
                            :class="{ 'rv-line--active': selectedKey === 'l:' + line.row_no, ['rv-line--' + statusClass(line)]: true }">
                            <div class="rv-line-head" @click="selectLine(line)">
                                <span class="rv-line-no">{{ line.row_no }}</span>
                                <span class="rv-line-desc">{{ line.description || '(no description)' }}</span>
                                <span class="rv-line-status" :class="'rv-line-status--' + statusClass(line)">{{ line.resolution_status }}</span>
                            </div>

                            <div class="rv-line-grid">
                                <label>Qty<input type="text" class="rv-input rv-input--sm" v-model="line.qty" @change="persistLine(line, 'qty')" /></label>
                                <label>Rate<input type="text" class="rv-input rv-input--sm" v-model="line.rate" @change="persistLine(line, 'rate')" /></label>
                                <label>UOM<input type="text" class="rv-input rv-input--sm" v-model="line.uom" @change="persistLine(line, 'uom')" /></label>
                            </div>

                            <div class="rv-resolver">
                                <div class="rv-resolved" v-if="line.matched_item">
                                    <span class="rv-chip rv-chip--ok">{{ line.matched_item }}</span>
                                    <span v-if="line.match_method" class="rv-method">{{ line.match_method }} · {{ pct(line.match_confidence) }}</span>
                                </div>
                                <div v-else-if="line.resolution_status === 'Free Text'" class="rv-resolved">
                                    <span class="rv-chip">Free-text line</span>
                                </div>
                                <div v-else class="rv-unresolved">Unmatched — choose an item</div>

                                <div class="rv-cands" v-if="line.candidates && line.candidates.length">
                                    <button v-for="c in line.candidates.slice(0, 5)" :key="c.value" class="rv-cand"
                                        :class="{ 'rv-cand--on': c.value === line.matched_item }" @click="confirmItem(line, c.value)"
                                        :title="c.method + ' · ' + pct(c.score)">{{ c.label }} <em>{{ pct(c.score) }}</em></button>
                                </div>

                                <div class="rv-actions-row">
                                    <button class="rv-mini" @click="searchItem(line)">Search…</button>
                                    <button class="rv-mini" @click="newItem(line)">New Item</button>
                                    <button class="rv-mini" :class="{ 'rv-mini--on': line.resolution_status === 'Free Text' }" @click="freeText(line)">Free-text</button>
                                    <span v-if="line.supplier_code" class="rv-printed">code: {{ line.supplier_code }}</span>
                                    <span v-if="line.hsn" class="rv-printed">HSN: {{ line.hsn }}</span>
                                </div>
                            </div>
                        </div>
                    </template>
                </div>
            </div>

            <div class="rv-footer">
                <span v-if="createError" class="rv-err">{{ createError }}</span>
                <button class="rv-create" :disabled="creating" @click="createDocument">
                    <span v-if="creating"><i class="fa fa-spinner fa-spin"></i> Creating…</span>
                    <span v-else>Create {{ targetLabel }}</span>
                </button>
            </div>
        </template>
    </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

const props = defineProps({ extraction: { type: String, required: true } })
const emit = defineEmits(['created'])

const loading = ref(true)
const targetDoctype = ref(null)
const modelUsed = ref(null)
const pages = ref([])
const fields = ref([])
const lines = ref([])
const activePage = ref(1)
const selectedKey = ref(null)
const creating = ref(false)
const createError = ref(null)

const targetLabel = computed(() => __(targetDoctype.value || 'Document'))
const currentPage = computed(() => pages.value.find(p => p.page_no === activePage.value) || pages.value[0])
const resolvedLines = computed(() => lines.value.filter(l => l.matched_item || l.resolution_status === 'Free Text').length)
const unresolvedLines = computed(() => lines.value.filter(l => !l.matched_item && l.resolution_status !== 'Free Text').length)

const boxesOnPage = computed(() => {
    const pageNo = currentPage.value && currentPage.value.page_no
    const out = []
    fields.value.forEach(f => { if (f.bbox && f.source_page === pageNo) out.push({ key: 'f:' + f.fieldname, kind: 'field', label: f.label || f.fieldname, bbox: f.bbox }) })
    lines.value.forEach(l => { if (l.bbox && l.source_page === pageNo) out.push({ key: 'l:' + l.row_no, kind: 'line', label: '#' + l.row_no, bbox: l.bbox }) })
    return out
})

function pct(v) { return Math.round((v || 0) * 100) + '%' }
function confClass(c) { return (c || 0) >= 0.85 ? 'hi' : (c || 0) >= 0.6 ? 'mid' : 'lo' }
function statusClass(line) {
    if (line.matched_item) return 'ok'
    if (line.resolution_status === 'Free Text') return 'free'
    return 'warn'
}
function isChecked(f) { return f.value === '1' || f.value === 1 || f.value === true }
function boxStyle(b) { return { left: b.x * 100 + '%', top: b.y * 100 + '%', width: b.w * 100 + '%', height: b.h * 100 + '%' } }

function selectBox(box) {
    selectedKey.value = box.key
}
function selectField(f) { selectedKey.value = 'f:' + f.fieldname; if (f.bbox && f.source_page) activePage.value = f.source_page }
function selectLine(l) { selectedKey.value = 'l:' + l.row_no; if (l.bbox && l.source_page) activePage.value = l.source_page }

// ---- header field edits ----
function onCheck(f, ev) { f.value = ev.target.checked ? '1' : '0'; persist(f) }
async function persist(f) {
    try {
        const r = await frappe.call({ method: 'frappe_tools.api.doc_extract.update_extraction_field', args: { extraction: props.extraction, fieldname: f.fieldname, value: f.value } })
        if (r.message && r.message.status) f.status = r.message.status
    } catch (e) { frappe.show_alert({ message: __('Could not save {0}', [f.fieldname]), indicator: 'red' }, 5) }
}

// ---- link field (supplier) ----
async function pickLink(field, value) {
    field.value = value
    await persist(field)
}
function searchLink(field) {
    frappe.prompt([{ fieldname: 'rec', label: field.label, fieldtype: 'Link', options: field.link_doctype, reqd: 1 }],
        (v) => pickLink(field, v.rec), __('Select {0}', [field.label]), __('Select'))
}
function newSupplier(field) {
    frappe.prompt([
        { fieldname: 'supplier_name', label: 'Supplier Name', fieldtype: 'Data', default: field.llm_value || field.value, reqd: 1 },
        { fieldname: 'gstin', label: 'GSTIN', fieldtype: 'Data' },
        { fieldname: 'supplier_group', label: 'Supplier Group', fieldtype: 'Link', options: 'Supplier Group' },
    ], async (v) => {
        try {
            const r = await frappe.call({ method: 'frappe_tools.api.doc_resolve.create_supplier', args: { extraction: props.extraction, supplier_name: v.supplier_name, gstin: v.gstin, supplier_group: v.supplier_group } })
            if (r.message && r.message.supplier) { field.value = r.message.supplier; field.match_method = 'user-created' }
        } catch (e) { /* frappe shows the error */ }
    }, __('Create Supplier'), __('Create'))
}

// ---- line edits & resolution ----
async function persistLine(line, key) {
    try { await frappe.call({ method: 'frappe_tools.api.doc_extract.update_extraction_line', args: { extraction: props.extraction, row_no: line.row_no, [key]: line[key] } }) }
    catch (e) { frappe.show_alert({ message: __('Could not save line'), indicator: 'red' }, 5) }
}
async function confirmItem(line, itemCode) {
    try {
        await frappe.call({ method: 'frappe_tools.api.doc_resolve.confirm_line_item', args: { extraction: props.extraction, row_no: line.row_no, item_code: itemCode } })
        line.matched_item = itemCode; line.resolution_status = 'Confirmed'; line.match_method = 'user-confirmed'; line.match_confidence = 1
    } catch (e) { /* shown */ }
}
function searchItem(line) {
    frappe.prompt([{ fieldname: 'item', label: 'Item', fieldtype: 'Link', options: 'Item', reqd: 1 }],
        (v) => confirmItem(line, v.item), __('Select Item'), __('Select'))
}
function newItem(line) {
    frappe.prompt([
        { fieldname: 'item_name', label: 'Item Name', fieldtype: 'Data', default: line.description, reqd: 1 },
        { fieldname: 'item_group', label: 'Item Group', fieldtype: 'Link', options: 'Item Group', reqd: 1 },
        { fieldname: 'stock_uom', label: 'Default UOM', fieldtype: 'Link', options: 'UOM', default: line.uom || 'Nos', reqd: 1 },
        { fieldname: 'hsn', label: 'HSN / SAC', fieldtype: 'Data', default: line.hsn || '', description: 'Required on GST setups (6 or 8 digits).' },
    ], async (v) => {
        try {
            const r = await frappe.call({ method: 'frappe_tools.api.doc_resolve.create_item', args: { extraction: props.extraction, row_no: line.row_no, item_group: v.item_group, stock_uom: v.stock_uom, item_name: v.item_name, hsn: v.hsn } })
            if (r.message && r.message.item_code) { line.matched_item = r.message.item_code; line.resolution_status = 'New Item'; line.match_method = 'new-item'; line.match_confidence = 1 }
        } catch (e) { /* shown */ }
    }, __('Create Item'), __('Create'))
}
async function freeText(line) {
    try {
        await frappe.call({ method: 'frappe_tools.api.doc_resolve.set_line_freetext', args: { extraction: props.extraction, row_no: line.row_no } })
        line.matched_item = null; line.resolution_status = 'Free Text'
    } catch (e) { /* shown */ }
}

async function createDocument() {
    creating.value = true; createError.value = null
    try {
        const r = await frappe.call({ method: 'frappe_tools.api.doc_extract.create_document_from_extraction', args: { extraction: props.extraction } })
        emit('created', r.message)
    } catch (e) { createError.value = __('Could not create — resolve the supplier and any required fields, then retry.') }
    finally { creating.value = false }
}

async function load() {
    loading.value = true
    try {
        const r = await frappe.call({ method: 'frappe_tools.api.doc_extract.get_extraction', args: { extraction: props.extraction } })
        const m = r.message || {}
        targetDoctype.value = m.target_doctype; modelUsed.value = m.model_used
        pages.value = m.pages || []; fields.value = m.fields || []; lines.value = m.lines || []
        activePage.value = (pages.value[0] && pages.value[0].page_no) || 1
    } catch (e) { frappe.msgprint(__('Failed to load the extraction.')) }
    finally { loading.value = false }
}

onMounted(load)
</script>

<style scoped>
.rv-root { width: 100%; max-width: 1280px; margin: 0 auto; }
.rv-loading { display: flex; align-items: center; justify-content: center; gap: 12px; padding: 60px; color: #94a3b8; font-size: 13px; }
.rv-spinner { width: 22px; height: 22px; border: 2.5px solid #e2e8f0; border-top-color: #059669; border-radius: 50%; animation: rv-spin .7s linear infinite; }
@keyframes rv-spin { to { transform: rotate(360deg); } }

.rv-bar { display: flex; align-items: center; justify-content: space-between; padding: 10px 16px; background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; margin-bottom: 12px; }
.rv-bar-left { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.rv-bar-title { font-size: 13px; font-weight: 650; color: #1e293b; }
.rv-bar-sep { color: #cbd5e1; }
.rv-bar-muted { font-size: 12.5px; color: #64748b; }
.rv-summary { font-size: 12px; font-weight: 600; color: #059669; }
.rv-summary--warn { color: #d97706; }

.rv-grid { display: grid; grid-template-columns: 1.1fr 1fr; gap: 14px; align-items: start; }
.rv-doc { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px; position: sticky; top: 70px; }
.rv-pagetabs { display: flex; gap: 6px; margin-bottom: 10px; flex-wrap: wrap; }
.rv-pagetab { border: 1px solid #e2e8f0; background: #fff; color: #475569; font-size: 12px; font-weight: 600; padding: 4px 12px; border-radius: 6px; cursor: pointer; }
.rv-pagetab--active { background: #059669; border-color: #059669; color: #fff; }
.rv-img-wrap { position: relative; line-height: 0; border-radius: 8px; overflow: hidden; background: #0f172a; }
.rv-img { width: 100%; height: auto; display: block; }
.rv-box { position: absolute; border-radius: 2px; cursor: pointer; transition: all .12s ease; }
.rv-box--field { border: 2px solid rgba(217,119,6,.85); background: rgba(217,119,6,.10); }
.rv-box--line { border: 2px solid rgba(13,148,136,.85); background: rgba(13,148,136,.10); }
.rv-box--active { border-color: #4f46e5 !important; background: rgba(79,70,229,.22) !important; box-shadow: 0 0 0 2px rgba(79,70,229,.35); z-index: 2; }
.rv-box-tag { position: absolute; top: -18px; left: -2px; font-size: 10px; font-weight: 600; color: #fff; background: rgba(15,23,42,.8); padding: 2px 5px; border-radius: 3px; white-space: nowrap; opacity: 0; transition: opacity .12s; }
.rv-box:hover .rv-box-tag, .rv-box--active .rv-box-tag { opacity: 1; }
.rv-noimg { padding: 40px; text-align: center; color: #94a3b8; font-size: 13px; }

.rv-side { display: flex; flex-direction: column; gap: 8px; max-height: calc(100vh - 190px); overflow-y: auto; padding-right: 2px; }
.rv-section-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; color: #94a3b8; margin: 8px 0 2px; }

.rv-field, .rv-line { border: 1px solid #e2e8f0; border-radius: 9px; background: #fff; padding: 10px 12px; }
.rv-field--active, .rv-line--active { border-color: #4f46e5; box-shadow: 0 0 0 2px rgba(79,70,229,.12); }
.rv-field--rejected { opacity: .6; }
.rv-line--warn { border-left: 3px solid #d97706; }
.rv-line--ok { border-left: 3px solid #059669; }
.rv-line--free { border-left: 3px solid #94a3b8; }

.rv-field-head, .rv-line-head { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.rv-field-head { justify-content: space-between; margin-bottom: 6px; }
.rv-field-label { font-size: 12.5px; font-weight: 600; color: #1e293b; }
.rv-req { color: #dc2626; margin-left: 2px; }
.rv-conf { font-size: 11px; font-weight: 700; padding: 1px 7px; border-radius: 10px; }
.rv-conf--hi { background: #dcfce7; color: #15803d; } .rv-conf--mid { background: #fef3c7; color: #b45309; } .rv-conf--lo { background: #fee2e2; color: #b91c1c; }

.rv-input { width: 100%; border: 1px solid #e2e8f0; border-radius: 6px; padding: 6px 9px; font-size: 13px; color: #1e293b; background: #fff; }
.rv-input:focus { outline: none; border-color: #4f46e5; box-shadow: 0 0 0 2px rgba(79,70,229,.12); }
.rv-input--sm { padding: 4px 7px; font-size: 12px; }
.rv-check { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; color: #1e293b; }
.rv-raw { margin: 6px 0 0; font-size: 11.5px; color: #94a3b8; }
.rv-raw-label { font-weight: 600; color: #64748b; }

.rv-resolver { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.rv-resolved { display: flex; align-items: center; gap: 8px; }
.rv-chip { font-size: 12px; font-weight: 600; padding: 2px 9px; border-radius: 6px; background: #f1f5f9; color: #334155; }
.rv-chip--ok { background: #ecfdf5; color: #047857; }
.rv-method { font-size: 11px; color: #94a3b8; }
.rv-unresolved { font-size: 12px; color: #d97706; font-weight: 600; }
.rv-cands { display: flex; flex-wrap: wrap; gap: 5px; }
.rv-cand { border: 1px solid #e2e8f0; background: #fff; color: #475569; font-size: 11.5px; padding: 3px 9px; border-radius: 14px; cursor: pointer; }
.rv-cand:hover { border-color: #4f46e5; color: #4f46e5; }
.rv-cand--on { background: #4f46e5; border-color: #4f46e5; color: #fff; }
.rv-cand em { opacity: .7; font-style: normal; }
.rv-actions-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.rv-mini { border: 1px solid #e2e8f0; background: #fff; color: #64748b; font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 5px; cursor: pointer; }
.rv-mini:hover { border-color: #cbd5e1; color: #334155; }
.rv-mini--on { background: #64748b; border-color: #64748b; color: #fff; }
.rv-printed { font-size: 11px; color: #94a3b8; }

.rv-line-no { width: 20px; height: 20px; flex-shrink: 0; border-radius: 50%; background: #f1f5f9; color: #475569; font-size: 11px; font-weight: 700; display: inline-flex; align-items: center; justify-content: center; }
.rv-line-desc { flex: 1; font-size: 12.5px; font-weight: 600; color: #1e293b; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rv-line-status { font-size: 10px; font-weight: 700; padding: 1px 7px; border-radius: 10px; }
.rv-line-status--ok { background: #dcfce7; color: #15803d; } .rv-line-status--warn { background: #fef3c7; color: #b45309; } .rv-line-status--free { background: #f1f5f9; color: #64748b; }
.rv-line-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin: 8px 0; }
.rv-line-grid label { font-size: 10.5px; color: #94a3b8; font-weight: 600; display: flex; flex-direction: column; gap: 2px; }

.rv-footer { display: flex; align-items: center; justify-content: flex-end; gap: 14px; margin-top: 14px; padding: 12px 16px; background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; }
.rv-err { color: #b91c1c; font-size: 12.5px; }
.rv-create { height: 36px; padding: 0 22px; background: #059669; color: #fff; border: none; border-radius: 7px; font-size: 13px; font-weight: 650; cursor: pointer; }
.rv-create:hover:not(:disabled) { background: #047857; }
.rv-create:disabled { opacity: .6; cursor: not-allowed; }
</style>

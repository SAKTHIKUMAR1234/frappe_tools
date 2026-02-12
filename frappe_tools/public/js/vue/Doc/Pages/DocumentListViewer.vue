<template>
    <div class="doc-scanner-wrapper">
        <div class="header">
            <button v-if="has_create_permission" class="btn btn-secondary btn-sm" @click="fetchScannedDocumentsList">
                Refresh
            </button>
            <button v-if="has_create_permission" class="btn btn-primary btn-sm" @click="createNewDocument">Create
                New</button>

        </div>

        <div v-if="!documentsList.length" class="empty-state">
            <p>No scanned documents found.</p>
        </div>

        <div v-else class="documents-grid">
            <div v-for="doc in documentsList" :key="doc.name" class="document-card">
                <div class="doc-header">
                    <span class="doc-title">{{ doc.name }}</span>
                    <span class="doc-doctype">{{ doc._doctype }}</span>
                </div>

                <div class="doc-body">
                    <div class="row">
                        <span class="label">Linked Doc</span>
                        <span class="value">{{ doc._docname }}</span>
                    </div>

                    <div class="row">
                        <span class="label">Layout</span>
                        <span class="value">{{ doc.scanner_layout }}</span>
                    </div>

                    <div class="row">
                        <span class="label">Created</span>
                        <span class="value">{{ formatDate(doc.creation) }}</span>
                    </div>
                </div>

                <div class="doc-actions">
                    <button v-if="has_write_permission" class="btn btn-secondary btn-xs" @click="viewDocument(doc)">
                        View
                    </button>
                    <button v-if="has_print_permission" class="btn btn-primary btn-xs"
                        @click="printScannedDocuments(doc)">
                        PDF
                    </button>
                    <button v-if="has_delete_permission" class="btn btn-danger btn-xs"
                        @click="deleteScannedDocument(doc)">
                        Delete
                    </button>
                </div>
            </div>
        </div>
    </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue';

const props = defineProps({
    doctype: String,
    docname: String
});

const documentsList = ref([]);

function fetchScannedDocumentsList() {
    frappe.call({
        method: 'frappe_tools.api.doc_scanner.get_scanned_documents_list',
        args: {
            doctype: props.doctype,
            docname: props.docname
        },
        callback(response) {
            documentsList.value = response.message || [];
        }
    });
}

function viewDocument(doc) {
    frappe.open_in_new_tab = true;
    frappe.set_route(`document-scanner/${encodeURIComponent(doc._doctype)}/${encodeURIComponent(doc._docname)}/${encodeURIComponent(doc.name)}`);
}

function printScannedDocuments(doc) {
    const url = `/api/method/frappe_tools.frappe_tools.doctype.scanned_document.scanned_document.get_scan_pdf?`
        + `&name=${encodeURIComponent(doc.name)}`;

    window.open(url, "_blank");
}


function createNewDocument(doc) {
    frappe.open_in_new_tab = true;
    frappe.set_route(`document-scanner/${encodeURIComponent(props.doctype)}/${encodeURIComponent(props.docname)}/new`);
}

function deleteScannedDocument(doc) {
    frappe.confirm(
        `Delete scanned document ${doc.name}?`,
        () => {
            frappe.call({
                method: 'frappe_tools.api.doc_scanner.delete_scanned_docs',
                args: {
                    doc: doc.name
                },
                callback() {
                    fetchScannedDocumentsList();
                }
            });
        }
    );
}

function formatDate(date) {
    return frappe.datetime.str_to_user(date);
}

const has_write_permission = computed(() =>
    frappe.perm.has_perm(props.doctype, 0, 'write', props.docname)
);

const has_delete_permission = computed(() =>
    frappe.perm.has_perm(props.doctype, 0, 'delete', props.docname)
);

const has_create_permission = computed(() =>
    frappe.perm.has_perm(props.doctype, 0, 'create', props.docname)
);

const has_print_permission = computed(() => {
    return frappe.perm.has_perm(props.doctype, 0, 'print', props.docname);
})

onMounted(fetchScannedDocumentsList);
</script>

<style scoped>
.doc-scanner-wrapper {
    display: flex;
    flex-direction: column;
    gap: 14px;
}

.header {
    display: flex;
    justify-content: flex-end;
    align-items: center;
    gap: 8px;
}

.header .btn {
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    padding: 6px 16px;
    transition: all 0.15s ease;
}

.header .btn-primary {
    background: #4f46e5;
    border-color: #4f46e5;
}

.header .btn-primary:hover {
    background: #4338ca;
    border-color: #4338ca;
}

.header .btn-secondary {
    border: 1px solid #e2e8f0;
    color: #475569;
    background: #fff;
}

.header .btn-secondary:hover {
    background: #f8fafc;
    border-color: #cbd5e1;
}

.documents-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 12px;
}

.document-card {
    box-sizing: border-box;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 16px;
    background: #fff;
    display: flex;
    flex-direction: column;
    gap: 12px;
    transition: all 0.2s ease;
}

.document-card:hover {
    border-color: #cbd5e1;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04), 0 4px 12px rgba(0, 0, 0, 0.03);
}

.document-card * {
    line-height: 1.4;
}

.doc-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    width: 100%;
}

.doc-title {
    font-weight: 600;
    font-size: 13px;
    color: #1e293b;
}

.doc-doctype {
    font-size: 11px;
    color: #4f46e5;
    background: #eef2ff;
    padding: 2px 8px;
    border-radius: 20px;
    font-weight: 600;
}

.doc-body {
    display: flex;
    flex-direction: column;
    gap: 6px;
    width: 100%;
}

.row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 3px 0;
    font-size: 13px;
    width: 100%;
}

.label {
    color: #94a3b8;
    font-size: 12px;
}

.value {
    color: #1e293b;
    font-weight: 500;
    font-size: 12px;
}

.doc-actions {
    display: flex;
    justify-content: flex-end;
    gap: 6px;
    padding-top: 4px;
    border-top: 1px solid #f1f5f9;
}

.doc-actions .btn {
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    padding: 4px 12px;
    transition: all 0.15s ease;
}

.doc-actions .btn-secondary {
    border: 1px solid #e2e8f0;
    color: #475569;
    background: #fff;
}

.doc-actions .btn-secondary:hover {
    background: #f8fafc;
    border-color: #cbd5e1;
}

.doc-actions .btn-primary {
    background: #4f46e5;
    border-color: #4f46e5;
}

.doc-actions .btn-primary:hover {
    background: #4338ca;
    border-color: #4338ca;
}

.doc-actions .btn-danger {
    background: transparent;
    border: 1px solid #fca5a5;
    color: #ef4444;
}

.doc-actions .btn-danger:hover {
    background: #fef2f2;
    border-color: #ef4444;
}

.empty-state {
    text-align: center;
    padding: 40px 24px;
    color: #94a3b8;
    font-size: 14px;
    background: #f8fafc;
    border-radius: 10px;
    border: 1px dashed #e2e8f0;
}
</style>

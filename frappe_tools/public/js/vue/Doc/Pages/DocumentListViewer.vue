<template>
    <div class="doc-scanner-wrapper">
        <div class="header">
            <button v-if="has_create_permission" class="btn btn-secondary btn-sm" @click="fetchScannedDocumentsList">
                Refresh
            </button>
            <button v-if="has_create_permission" class="btn btn-primary btn-sm" @click="createNewDocument">Create
                New</button>

        </div>

        <!-- Empty State -->
        <div v-if="!documentsList.length" class="empty-state">
            <p>No scanned documents found.</p>
        </div>

        <!-- Documents List -->
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
                        Print
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
    const doctype = "Scanned Document";
    const url = `/tprint?doctype=${encodeURIComponent(doctype)}`
        + `&name=${encodeURIComponent(doc.name)}`
        + `&settings={"margin_top":10,"margin_bottom":10}`;

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
    frappe.perm.has_perm(cur_frm.doc.doctype, 0, 'write', cur_frm.doc.name)
);

const has_delete_permission = computed(() =>
    frappe.perm.has_perm(cur_frm.doc.doctype, 0, 'delete', cur_frm.doc.name)
);

const has_create_permission = computed(() =>
    frappe.perm.has_perm(cur_frm.doc.doctype, 0, 'create', cur_frm.doc.name)
);

const has_print_permission = computed(() => {
    return frappe.perm.has_perm(cur_frm.doc.doctype, 0, 'print', cur_frm.doc.name);
})

onMounted(fetchScannedDocumentsList);
</script>

<style scoped>
.doc-scanner-wrapper {
    display: flex;
    flex-direction: column;
    gap: 12px;
}

.header {
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.documents-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 12px;
}

.document-card {
    box-sizing: border-box;
    border: 1px solid var(--border-color);
    border-radius: 6px;
    padding: 15px;
    background: var(--card-bg);
    display: flex;
    flex-direction: column;
    gap: 12px;
}

.document-card * {
    line-height: 1.4;
}

.doc-header {
    display: flex;
    justify-content: space-between;
    font-weight: 600;
    width: 90%;
}

.doc-doctype {
    font-size: 12px;
    color: var(--text-muted);
}

.doc-body {
    display: flex;
    flex-direction: column;
    gap: 6px;
    width: 95%;
    align-items: center;
}

.row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 2px 0;
    font-size: 13px;
    width: 100%;
}

.label {
    color: var(--text-muted);
}

.doc-actions {
    display: flex;
    justify-content: flex-end;
    gap: 6px;
}

.empty-state {
    text-align: center;
    padding: 24px;
    color: var(--text-muted);
}
</style>

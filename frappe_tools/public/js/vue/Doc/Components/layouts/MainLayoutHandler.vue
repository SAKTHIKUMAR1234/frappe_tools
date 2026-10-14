<template>
    <div class="main-content" ref="root">
        <div class="action_section">
            <button class="btn btn-primary btn-sm" :disabled="isSaving" @click.prevent="save">
                {{ props.is_new ? 'Create' : 'Update' }}
            </button>
            <button v-if="!props.is_new" class="btn btn-default btn-sm" @click.prevent="emit('newScan')">
                Create
            </button>
        </div>
        <div ref="layout_select_ref" class="layout_select_ref">
        </div>
        <div v-if="dragMem.document_scanner_details.sections && Object.keys(dragMem.document_scanner_details.sections).length > 0"
            v-for="value in Object.keys(dragMem.document_scanner_details.sections)" :key="value"
            :data-section="value" :class="{ 'scan-focused': isScanFocused(value) }"
            @click="dragMem.focusSection(value)">
            <component :is="LayoutMapper[dragMem.document_scanner_details.sections[value].section_type]" v-bind="{
                title: value,
                is_new: props.is_new,
                images: dragMem.document_scanner_details.sections[value].images
            }" @addPage="(pages) => addToPages(value, pages)" @removePage="(page_no) => removeOnPage(value, page_no)"
                @removeImage="([page_no, page_type]) => removeOnImage(value, page_no, page_type)" />
        </div>
    </div>
</template>

<script setup>
import FrontBackHorizontal from './FrontBackHorizontal.vue';
import FrontBackVertical from './FrontBackVertical.vue';
import SinglePage from './SinglePage.vue';
import SeriesVertical from './SeriesVertical.vue';
import { reactive, ref, onMounted, watch } from 'vue';
import { frappeCallAsync, imageToDataURL, oneAtATime } from '../../../utils';
import { useGloabalDragMemory } from '../../../Store/doc_scanner_drag_drop_memory';
import { storedPages, refreshSavedDetails } from '../../../Store/scan_pages';

const props = defineProps({
    is_new: {
        type: Boolean,
        default: true
    },
    document_name: {
        type: String,
        default: null
    },
    doctype: {
        type: String,
        default: null
    },
    scan_name: {
        type: String,
        default: null
    }
})

const emit = defineEmits(['newScan', 'created']);

const dragMem = useGloabalDragMemory();
const root = ref(null);
const currScannerDetails = ref(null);
const layout_select = ref(null);
const localScanName = ref(props.scan_name);
const isSaving = ref(false);

watch(() => props.is_new, (val) => {
    if (val) {
        localScanName.value = null;
        if (layout_select.value) {
            layout_select.value.set_value('');
        }
    }
}, { immediate: true });

watch(() => props.scan_name, (val) => {
    localScanName.value = val;
});

const LayoutMapper = {
    "Front And Back Vertical": FrontBackVertical,
    "Front And Back Horizontal": FrontBackHorizontal,
    "Single Page": SinglePage,
    "Series Vertical": SeriesVertical
};

function isScanFocused(value) {
    return Boolean(dragMem.document_scanner_details.auto_capture) && dragMem.focused_section === value;
}

// Edit the live sections in place: a scan placed while a copy was being saved back would be lost.
function addToPages(value, pages) {
    const images = dragMem.document_scanner_details.sections[value].images;
    images.push(...pages);
    dragMem.renumberPages(images);
}

function removeOnPage(value, page_no) {
    const images = dragMem.document_scanner_details.sections[value].images;

    let removalIndex = [];

    for (let i = 0; i < images.length; i++) {
        if (page_no === images[i].page_no) {
            removalIndex.push(i);
        }
    }

    for (const i of removalIndex.sort((a, b) => b - a)) {
        const [image_details] = images.splice(i, 1);

        if (image_details && image_details.attachment) {
            sendAttachementDetails(image_details);
        }
    }

    dragMem.renumberPages(images);
}

function sendAttachementDetails(image) {
    dragMem.setImagesDetails([...dragMem.imagesList, { attachment: image.attachment, detail: image.detail || null }]);
}



function removeOnImage(value, page_no, page_type) {
    const images = dragMem.document_scanner_details.sections[value].images;
    const image = images.find((img) => img.page_no == page_no && img.page_type == page_type);
    if (image?.attachment) {
        sendAttachementDetails(image);
        Object.assign(image, { attachment: null, detail: null });
    }
}

// Returns one { image, attachment, sent_detail, detail } per saved image; a stored page is sent as its detail.
async function uploadImagesWithConcurrency(sections, layout, concurrency = 2) {
    const saved = [];
    const tasks = [];

    for (const section of Object.keys(sections)) {
        const body_data = {
            title: section,
            layout_type: sections[section].section_type,
            layout
        };
        for (const image of sections[section].images) {
            const req_body = {
                ...body_data,
                page_no: image.page_no,
                detail: image.attachment ? image.detail || null : null,
                attachment: image.detail ? null : image.attachment,
                page_type: image.page_type
            };
            const attachment = image.attachment;
            tasks.push(() =>
                frappeCallAsync("frappe_tools.api.doc_scanner.upload_image", {
                    image_data: JSON.stringify(req_body)
                }).then(r => saved.push({ image, attachment, sent_detail: req_body.detail, detail: r.message }))
            );
        }
    }

    async function runTasksLimited(tasks, concurrency) {
        let index = 0;
        async function worker() {
            while (index < tasks.length) {
                const current = index++;
                await tasks[current]();
            }
        }
        const workers = Array(concurrency).fill(null).map(worker);
        await Promise.all(workers);
    }

    await runTasksLimited(tasks, concurrency);
    return saved;
}

function setSavedDetails(saved) {
    const sections = Object.values(dragMem.document_scanner_details.sections || {});
    refreshSavedDetails(saved, [...sections.flatMap((section) => section.images), ...dragMem.imagesList]);
}


async function validateUpdadeScannedDetails() {
    let dataToSend = dragMem.document_scanner_details.sections;
    let layout = dragMem.document_scanner_details.layout;

    if (!dataToSend || !layout) {
        frappe.msgprint(__('Please select a Document Layout first.'));
        return;
    }

    // Validation: Check if there's at least one image
    let hasImages = false;
    for (const section of Object.values(dataToSend)) {
        if (section.images && section.images.some(img => img.attachment)) {
            hasImages = true;
            break;
        }
    }

    if (!hasImages) {
        frappe.msgprint(__('Please add at least one image to create/update the document.'));
        return;
    }

    const saved = await uploadImagesWithConcurrency(dataToSend, layout, 5);
    const r = await frappeCallAsync("frappe_tools.api.doc_scanner.make_or_update_main_doc", {
        "layout": layout,
        "doctype": props.doctype,
        "docname": props.document_name,
        "is_new": props.is_new,
        "documents": JSON.stringify(saved.map((row) => row.detail)),
        "scan_name": localScanName.value
    });
    localScanName.value = r.message;
    setSavedDetails(saved);
    if (props.is_new) {
        frappe.show_alert({ message: __('Document Created Successfully'), indicator: 'green' });
        // Update URL without reload to preserve WebRTC
        const newPath = `document-scanner/${encodeURIComponent(props.doctype)}/${encodeURIComponent(props.document_name)}/${encodeURIComponent(r.message)}`;
        window.history.replaceState(null, '', `/app/${newPath}`);
        emit('created');
    } else {
        frappe.show_alert({ message: __('Document Updated Successfully'), indicator: 'green' });
    }
}

// Two saves at once would each retire the other's details, so a save waits for the last one to end.
const save = oneAtATime(validateUpdadeScannedDetails, isSaving);

function createLinkComponent() {
    const el = root.value;
    layout_select.value = frappe.ui.form.make_control({
        parent: $(el).find('.layout_select_ref'),
        df: {
            fieldtype: "Link",
            fieldname: "document_layout",
            label: "Select Document Layout",
            options: 'Document Scanner Layout',
            reqd: 1,
            onchange: function () {
                let val = layout_select.value.get_value();
                if (val) {
                    fetchLayoutDetails(val);
                }
            },
            get_query: () => {
                return {
                    filters: {
                        layout_doctype: props.doctype,
                        docstatus: 1
                    }
                };
            }
        },
        doc: {},
        render_input: true,
    });
    if (!props.is_new) {
        layout_select.value.df.read_only = 1
        layout_select.value.refresh();
    }
}

function getSectionDetails(sections) {
    let section_details = {};
    for (let section of sections) {
        section_details[section.title] = {
            "section_type": section.layout_type,
            "images": []
        }
    }
    return section_details;
}

const constructLayoutDetails = async (layout_doc) => {
    dragMem.focusSection(null);
    await dragMem.setDocScannerDetails({
        "layout": layout_doc.name,
        "auto_capture": layout_doc.auto_capture,
        "sections": getSectionDetails(layout_doc.layout_doctype_sections)
    })
    if (currScannerDetails.value) {
        setupDocumentScannerDetail(currScannerDetails.value);
    }
}

const fetchLayoutDetails = async (layout_name) => {
    const layout_doc = await frappe.db.get_doc('Document Scanner Layout', layout_name);
    await constructLayoutDetails(layout_doc);
};

// Converts stored images before touching the store, then merges them into the
// live sections in place, so a scan received while loading is kept after them.
async function setupDocumentScannerDetail(details) {
    if (!layout_select.value) return;

    const attachments = await Promise.all(details['attachments'].map(async (ele) => ({
        ...ele,
        "attachment": ele['attachment'] ? await imageToDataURL(ele['attachment']) : null,
    })));

    for (const [key, section] of Object.entries(dragMem.document_scanner_details.sections)) {
        const stored = storedPages(attachments, key, section.section_type);
        if (!stored.length) continue;

        const offset = Math.max(...stored.map((img) => img.page_no));
        section.images.forEach((img) => { img.page_no += offset; });
        section.images.unshift(...stored);
        dragMem.renumberPages(section.images);
        if (section.section_type === "Single Page") {
            moveToTray(section.images.splice(stored.length));
        }
    }
}

// A Single Page section shows one image: a scan placed there while loading goes back to the tray.
function moveToTray(images) {
    const attached = images
        .filter((img) => img.attachment)
        .map((img) => ({ attachment: img.attachment, detail: img.detail || null }));
    if (attached.length) {
        dragMem.setImagesDetails([...dragMem.imagesList, ...attached]);
        frappe.show_alert({ message: __('A scan placed while loading was moved to the tray.'), indicator: 'orange' });
    }
}

async function fetchAndSetScanDetails() {
    frappe.call({
        "method": "frappe_tools.api.doc_scanner.load_scanned_document_details",
        "args": {
            "docname": localScanName.value
        },
        "callback": (r) => {
            currScannerDetails.value = r.message;
            layout_select.value.set_value(r.message['layout']);
        },
        "freeze": true,
        "freeze_msg": "Fetching Details..."
    })
}

onMounted(async () => {
    createLinkComponent();
    if (localScanName.value && localScanName.value !== 'new') {
        await fetchAndSetScanDetails();
    }
});

// Exposed so the parent can trigger Save via the Ctrl/Cmd+Enter shortcut.
defineExpose({ save });

</script>

<style scoped>
.main-content {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 0;
    flex: 1;
}

.action_section {
    display: flex;
    flex-direction: row;
    justify-content: flex-end;
    gap: 8px;
    padding: 8px 4px;
    position: sticky;
    top: 0;
    z-index: 10;
    background: #fff;
}

.scan-focused :deep(.doc-section-card) {
    border-color: #4f46e5;
    box-shadow: 0 0 0 1px #4f46e5;
}

.scan-focused :deep(.doc-section-header) {
    background: #eef2ff;
}

.action_section .btn {
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    padding: 6px 18px;
    transition: all 0.15s ease;
}

.action_section .btn-primary {
    background: #4f46e5;
    border-color: #4f46e5;
}

.action_section .btn-primary:hover {
    background: #4338ca;
    border-color: #4338ca;
}

.action_section .btn-default {
    border: 1px solid #e2e8f0;
    color: #475569;
    background: #fff;
}

.action_section .btn-default:hover {
    background: #f8fafc;
    border-color: #cbd5e1;
}
</style>
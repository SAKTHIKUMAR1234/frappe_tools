<template>
    <div class="main-content" ref="root">
        <div class="action_section">
            <button class="btn btn-primary btn-sm" @click.prevent="validateUpdadeScannedDetails">
                {{ props.is_new ? 'Create' : 'Update' }}
            </button>
        </div>
        <div ref="layout_select_ref" class="layout_select_ref">
        </div>
        <div v-if="dragMem.document_scanner_details.sections && Object.keys(dragMem.document_scanner_details.sections).length > 0"
            v-for="value in Object.keys(dragMem.document_scanner_details.sections)" :key="value">
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
import { frappeCallAsync } from '../../../utils';
import { useGloabalDragMemory } from '../../../Store/doc_scanner_drag_drop_memory';

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

const LayoutMapper = {
    "Front And Back Vertical": FrontBackVertical,
    "Front And Back Horizontal": FrontBackHorizontal,
    "Single Page": SinglePage,
    "Series Vertical": SeriesVertical
};

const dragMem = useGloabalDragMemory();

const root = ref(null);

const currScannerDetails = ref(null);
const layout_select = ref(null);

async function addToPages(value, pages) {
    let reference = JSON.parse(JSON.stringify(dragMem.document_scanner_details));
    reference['sections'][value]['images'].push(...pages);
    await dragMem.setDocScannerDetails(reference);
}

async function removeOnPage(value, page_no) {
    let reference = JSON.parse(JSON.stringify(dragMem.document_scanner_details));
    const images = reference.sections[value].images;

    let removalIndex = [];

    for (let i = 0; i < images.length; i++) {
        if (page_no === images[i].page_no) {
            removalIndex.push(i);
        }
    }

    for (const i of removalIndex.sort((a, b) => b - a)) {
        const [image_details] = images.splice(i, 1);

        if (image_details && image_details.attachment) {
            sendAttachementDetails(image_details.attachment);
        }
    }

    await dragMem.setDocScannerDetails(reference);
}

function sendAttachementDetails(image) {
    dragMem.setImagesDetails([...dragMem.imagesList, image]);
}



async function removeOnImage(value, page_no, page_type) {
    let reference = JSON.parse(JSON.stringify(dragMem.document_scanner_details));
    const images = reference.sections[value].images;
    let removable = null;
    for (let i = 0; i < images.length; i++) {
        if (page_no == images[i]['page_no'] && images[i]['page_type'] == page_type) {
            removable = images[i]['attachment'];
            images[i]['attachment'] = null
            break;
        }
    }
    reference.sections[value].images = images;
    if (removable) {
        sendAttachementDetails(removable);
    }
    await dragMem.setDocScannerDetails(reference);
}

async function uploadImagesWithConcurrency(sections, layout, concurrency = 2) {
    let attachmentNames = [];
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
                attachment: image.attachment,
                page_type: image.page_type
            };
            tasks.push(() =>
                frappeCallAsync("frappe_tools.api.doc_scanner.upload_image", {
                    image_data: JSON.stringify(req_body)
                }).then(r => attachmentNames.push(r.message))
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
    return attachmentNames;
}


async function validateUpdadeScannedDetails() {
    let dataToSend = dragMem.document_scanner_details.sections;
    let layout = dragMem.document_scanner_details.layout;
    let attachmentNames = await uploadImagesWithConcurrency(dataToSend, layout, 5);
    frappe.call({
        "method": "frappe_tools.api.doc_scanner.make_or_update_main_doc",
        "args": {
            "layout": layout,
            "doctype": props.doctype,
            "docname": props.document_name,
            "is_new": props.is_new,
            "documents": JSON.stringify(attachmentNames),
            "scan_name" : props.scan_name
        },
        "callback": (r) => {
            if(props.is_new){
                frappe.set_route(`document-scanner/${encodeURIComponent(props.doctype)}/${encodeURIComponent(props.document_name)}/${encodeURIComponent(r.message)}`)
            } else {
                window.location.reload();
            }
        },
        freeze : true,
        freeze_msg : "Please Wait While Processing"
    })
}

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
                        docstatus : 1
                    }
                };
            }
        },
        doc: this.sample_doc,
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
    await dragMem.setDocScannerDetails({
        "layout": layout_doc.name,
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

function setupDocumentScannerDetail(details) {
    if (layout_select.value) {
        let imageData = {};
        for (let i = 0; i < details['attachments'].length; i++) {
            let ele = details['attachments'][i];
            if (!imageData[ele['title']]) {
                imageData[ele['title']] = {}
            }
            if (!imageData[ele['title']][ele['layout_type']]) {
                imageData[ele['title']][ele['layout_type']] = [];
            }
            imageData[ele['title']][ele['layout_type']].push({
                "attachment": ele['attachment'],
                "page_no": ele['page_no'],
                "page_type": ele['page_type']
            })
        }

        let curr_details = JSON.parse(JSON.stringify(dragMem.document_scanner_details));
        let keys = Object.keys(curr_details['sections']);
        for (let key of keys) {
            if (imageData[key] && imageData[key][curr_details['sections'][key]['section_type']]) {
                curr_details['sections'][key]['images'] = [...curr_details['sections'][key]['images'], ...imageData[key][curr_details['sections'][key]['section_type']]]
            }
        }
        dragMem.setDocScannerDetails(curr_details);
    }
}

async function fetchAndSetScanDetails() {
    frappe.call({
        "method": "frappe_tools.api.doc_scanner.load_scanned_document_details",
        "args": {
            "docname": props.scan_name
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
    if (!props.is_new && props.scan_name) {
        await fetchAndSetScanDetails();
    }
});

</script>

<style scoped>
.main-content {
    display: flex;
    flex-direction: column;
    gap: 5px;
    padding: 10px;
    flex: 1;
}

.action_section {
    display: flex;
    flex-direction: row;
    justify-content: end;
    gap: 50px;
}
</style>
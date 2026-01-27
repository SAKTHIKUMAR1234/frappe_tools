<template>
    <div class="doc-section-card">
        <div class="doc-section-header">
            <span>{{ title }}</span>
            <button class="icon-btn primary" title="Add Layout" @click="onAddLayout">
                <i class="pi octicon octicon-plus"></i> <span>Add Layout</span>
            </button>
        </div>

        <div class="doc-section-body">
            <div class="doc-list">
                <div v-for="(page, index) in imagePairs" :key="index" class="doc-card">
                    
                    <button class="remove-page-icon" title="Remove Page" @click="onRemovePage(index)">
                        <i class="pi octicon octicon-x"></i>
                    </button>

                    <div class="doc-page front">
                        <div class="page-label">Front</div>
                        <div class="doc-page-content">
                            <img v-if="page.front" :src="page.front" :draggable="true"
                                @dragstart.stop="dragHandler.dragStart(props.title, page.page_no, 'Front')"
                                @dragend.stop="dragHandler.dragEnd()" />
                            <div v-else class="empty-page" :draggable="true"
                                @dragover.stop="dragHandler.dragHover(props.title, page.page_no, 'Front')">
                                <i class="pi octicon octicon-download"></i>
                            </div>

                            <button v-if="page.front" class="img-remove-overlay" @click="onRemoveImage(index, 'front')">
                                <i class="pi octicon octicon-trash"></i>
                            </button>
                        </div>
                    </div>

                    <div class="doc-page back">
                        <div class="page-label">Back</div>
                        <div class="doc-page-content">
                            <img v-if="page.back" :src="page.back" :draggable="true"
                                @dragstart.stop="dragHandler.dragStart(props.title, page.page_no, 'Back')"
                                @dragend.stop="dragHandler.dragEnd()" />
                            <div v-else class="empty-page" :draggable="true"
                                @dragover.stop="dragHandler.dragHover(props.title, page.page_no, 'Back')">
                                <i class="pi octicon octicon-download"></i>
                            </div>

                            <button v-if="page.back" class="img-remove-overlay" @click="onRemoveImage(index, 'back')">
                                <i class="pi octicon octicon-trash"></i>
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</template>
<script setup>
import { computed, watch } from 'vue';
import { useGloabalDragMemory } from '../../../Store/doc_scanner_drag_drop_memory';

const dragHandler = useGloabalDragMemory();

const props = defineProps({
    images: {
        type: Array,
        default: () => [],
    },
    title: String,
    is_new: Boolean,
});

watch(() => props.images, () => { }, {
    deep: true
})

const emit = defineEmits([
    'addPage',
    'removePage',
    'removeImage',
]);

const imagePairs = computed(() => {
    const map = {};

    props.images.forEach(img => {
        if (!map[img.page_no]) {
            map[img.page_no] = {
                page_no: img.page_no,
                front: null,
                back: null,
            };
        }

        if (img.page_type === 'Front') {
            map[img.page_no].front = img.attachment;
        }

        if (img.page_type === 'Back') {
            map[img.page_no].back = img.attachment;
        }
    });

    return Object.values(map).sort((a, b) => a.page_no - b.page_no);
});

const onAddLayout = () => {
    emit('addPage', [
        {
            "page_no": imagePairs.value.length + 1,
            "page_type": 'Front',
            "attachment": null
        },
        {
            "page_no": imagePairs.value.length + 1,
            "page_type": "Back",
            "attchment": null
        }
    ]);
};

const onRemoveImage = (pageIndex, side) => {
    emit('removeImage', [
        imagePairs.value[pageIndex].page_no,
        side === 'front' ? 'Front' : 'Back',
    ]);
};

const onRemovePage = (pageIndex) => {
    emit('removePage', imagePairs.value[pageIndex].page_no);
};
</script>


<style scoped>
.doc-section-card {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    background: #fff;
    width: 100%;
}

.doc-section-header {
    padding: 8px 12px;
    font-weight: 600;
    background: #f8fafc;
    border-bottom: 1px solid #e2e8f0;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.doc-section-body {
    padding: 12px;
}

.doc-list {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
}

/* COMPACT PAGE CARD */
.doc-card {
    position: relative;
    width: 180px; /* Slimmer for vertical stacking */
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 8px;
    background: #fff;
    transition: box-shadow 0.2s;
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.doc-card:hover {
    box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);
}

.doc-page {
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.page-label {
    font-size: 10px;
    text-transform: uppercase;
    color: #94a3b8;
    font-weight: bold;
    text-align: center;
}

.doc-page-content {
    position: relative;
    height: 120px; /* Adjust height as needed */
    border: 1px solid #f1f5f9;
    background: #f8fafc;
    border-radius: 4px;
    overflow: hidden;
}

.doc-page-content img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.empty-page {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #cbd5e1;
    border: 2px dashed #e2e8f0;
}

/* BUTTONS & OVERLAYS */
.icon-btn {
    border: none;
    background: transparent;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 13px;
    color: #3b82f6;
    padding: 4px 8px;
    border-radius: 4px;
}

.icon-btn:hover { background: #eff6ff; }

.img-remove-overlay {
    position: absolute;
    top: 4px;
    right: 4px;
    background: rgba(239, 68, 68, 0.9);
    color: white;
    border: none;
    border-radius: 4px;
    width: 24px;
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transition: opacity 0.2s;
    cursor: pointer;
}

.doc-page-content:hover .img-remove-overlay {
    opacity: 1;
}

.remove-page-icon {
    position: absolute;
    top: -8px;
    right: -8px;
    background: #f1f5f9;
    border: 1px solid #cbd5e1;
    border-radius: 50%;
    width: 20px;
    height: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    color: #64748b;
    cursor: pointer;
    z-index: 10;
}

.remove-page-icon:hover {
    background: #fee2e2;
    color: #ef4444;
}
</style>
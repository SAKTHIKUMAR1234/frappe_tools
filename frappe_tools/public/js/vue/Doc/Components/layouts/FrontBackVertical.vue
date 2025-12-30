<template>
    <div class="doc-section-card">
        <div class="doc-section-header">
            {{ title }}
        </div>

        <button class="btn btn-primary btn-sm m-2" @click="onAddLayout">
            Add Layout
        </button>
        <div class="doc-section-body">
            <div class="doc-list">
                <div v-for="(page, index) in imagePairs" :key="index" class="doc-card">
                    <div class="doc-page front">
                        <div class="doc-page-content">
                            <img v-if="page.front" :src="page.front" :draggable="true"
                                @dragstart.stop="dragHandler.dragStart(props.title, page.page_no, 'Front')"
                                @dragend.stop="dragHandler.dragEnd()" />
                            <div v-else class="empty-page" :draggable="true"
                                @dragover.stop="dragHandler.dragHover(props.title, page.page_no, 'Front')">
                                Front
                            </div>
                        </div>

                        <div class="doc-page-actions" v-if="page.front">
                            <button class="btn btn-danger btn-sm" @click="onRemoveImage(index, 'front')">
                                Remove
                            </button>
                        </div>
                    </div>
                    <div class="doc-page back">
                        <div class="doc-page-content">
                            <img v-if="page.back" :src="page.back" :draggable="true"
                                @dragstart.stop="dragHandler.dragStart(props.title, page.page_no, 'Back')"
                                @dragend.stop="dragHandler.dragEnd()" />
                            <div v-else class="empty-page" :draggable="true"
                                @dragover.stop="dragHandler.dragHover(props.title, page.page_no, 'Back')">
                                Back
                            </div>
                        </div>

                        <div class="doc-page-actions" v-if="page.back">
                            <button class="btn btn-danger btn-sm" @click="onRemoveImage(index, 'back')">
                                Remove
                            </button>
                        </div>
                    </div>

                    <button class="btn btn-outline-danger btn-sm w-100 remove-page-btn" @click="onRemovePage(index)">
                        Remove Page
                    </button>

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
    border: 1px solid #dcdcdc;
    border-radius: 8px;
    background: #ffffff;
    max-width: 1500px;
    width: 100%;
    overflow: hidden;
}

.doc-section-header {
    padding: 12px 16px;
    font-weight: 600;
    border-bottom: 1px solid #e5e5e5;
    background: #f7f7f7;
}

.doc-section-body {
    padding: 16px;
}

.doc-list {
    display: flex;
    flex-wrap: wrap;
    gap: 20px;
}

/* Page Card */
.doc-card {
    width: 230px;
    border: 1px solid #d0d0d0;
    border-radius: 6px;
    padding: 10px;
    background: #fafafa;
    display: flex;
    flex-direction: column;
    gap: 10px;
}

/* Front / Back container */
.doc-page {
    border: 1px dashed #bdbdbd;
    background: #ffffff;
    border-radius: 4px;
    height: 160px;
    display: flex;
    flex-direction: column;
}

/* Image area */
.doc-page-content {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
}

/* Image fit fix */
.doc-page img {
    max-width: 100%;
    max-height: 100%;
    width: auto;
    height: auto;
    object-fit: contain;
}

/* Empty drop area */
.empty-page {
    width: 100%;
    height: 100%;
    border: 2px dashed #d0d0d0;
    border-radius: 4px;
    color: #999;
    font-size: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #fcfcfc;
}

/* Button alignment */
.doc-page-actions {
    padding: 6px;
    display: flex;
    justify-content: center;
}

/* Remove page button */
.remove-page-btn {
    margin-top: 6px;
}

.doc-page.front {
    background: #ffffff;
}

.doc-page.back {
    background: #fdfdfd;
}
</style>

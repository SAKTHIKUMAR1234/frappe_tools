<template>
    <div class="doc-section-card">
        <div class="doc-section-header">
            {{ title }}
        </div>

        <button class="btn btn-primary btn-sm m-2" @click="onAddLayout">
            Add Layout
        </button>

        <div class="doc-section-body">
            <div class="doc-list vertical">
                <div
                    v-for="(page, index) in pages"
                    :key="page.page_no"
                    class="doc-card"
                >
                    <div class="doc-page front">
                        <div class="doc-page-content">
                            <img
                                v-if="page.front"
                                :src="page.front"
                                :draggable="true"
                                @dragstart.stop="dragHandler.dragStart(title, page.page_no, 'Front')"
                                @dragend.stop="dragHandler.dragEnd()"
                            />
                            <div
                                v-else
                                class="empty-page"
                                :draggable="true"
                                @dragover.stop="dragHandler.dragHover(title, page.page_no, 'Front')"
                            >
                                Page
                            </div>
                        </div>

                        <div class="doc-page-actions" v-if="page.front">
                            <button
                                class="btn btn-danger btn-sm"
                                @click="onRemoveImage(index)"
                            >
                                Remove
                            </button>
                        </div>
                    </div>

                    <button
                        class="btn btn-outline-danger btn-sm w-100 remove-page-btn"
                        @click="onRemovePage(index)"
                    >
                        Remove Page
                    </button>
                </div>
            </div>
        </div>
    </div>
</template>

<script setup>
import { computed } from 'vue'
import { useGloabalDragMemory } from '../../../Store/doc_scanner_drag_drop_memory'

const dragHandler = useGloabalDragMemory()

const props = defineProps({
    images: { type: Array, default: () => [] },
    title: String,
})

const emit = defineEmits([
    'addPage',
    'removePage',
    'removeImage',
])

const pages = computed(() => {
    const map = {}

    props.images.forEach(img => {
        if (!map[img.page_no]) {
            map[img.page_no] = {
                page_no: img.page_no,
                front: null,
            }
        }

        if (img.page_type === 'Front') {
            map[img.page_no].front = img.attachment
        }
    })

    return Object.values(map).sort((a, b) => a.page_no - b.page_no)
})

const onAddLayout = () => {
    emit('addPage', [
        {
            page_no: pages.value.length + 1,
            page_type: 'Front',
            attachment: null,
        },
    ])
}

const onRemoveImage = (pageIndex) => {
    emit('removeImage', [
        pages.value[pageIndex].page_no,
        'Front',
    ])
}

const onRemovePage = (pageIndex) => {
    emit('removePage', pages.value[pageIndex].page_no)
}
</script>

<style scoped>
.doc-list.vertical {
    display: flex;
    flex-direction: column;
    gap: 20px;
}


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

.doc-page {
    border: 1px dashed #bdbdbd;
    background: #ffffff;
    border-radius: 4px;
    height: 160px;
    display: flex;
    flex-direction: column;
}

.doc-page-content {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
}

.doc-page img {
    max-width: 100%;
    max-height: 100%;
    width: auto;
    height: auto;
    object-fit: contain;
}

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

.doc-page-actions {
    padding: 6px;
    display: flex;
    justify-content: center;
}

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

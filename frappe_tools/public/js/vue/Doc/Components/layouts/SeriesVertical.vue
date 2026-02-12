<template>
    <div class="doc-section-card">
        <div class="doc-section-header">
            <span>{{ title }}</span>
            <button class="icon-btn primary" title="Add Layout" @click="onAddLayout">
                <i class="pi octicon octicon-plus"></i> <span>Add Page</span>
            </button>
        </div>

        <div class="doc-section-body">
            <div class="doc-list vertical">
                <div
                    v-for="(page, index) in pages"
                    :key="page.page_no"
                    class="doc-card"
                >
                    <button class="remove-page-icon" title="Remove Page" @click="onRemovePage(index)">
                        <i class="pi octicon octicon-x"></i>
                    </button>

                    <div class="doc-page front">
                        <div class="page-label">Page {{ page.page_no }}</div>
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
                                <i class="pi octicon octicon-download"></i>
                            </div>

                            <button v-if="page.front" class="img-remove-overlay" @click="onRemoveImage(index)">
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
.doc-section-card {
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    background: #fff;
    width: 100%;
    overflow: hidden;
}

.doc-section-header {
    padding: 10px 16px;
    font-weight: 600;
    font-size: 13px;
    color: #1e293b;
    background: #fff;
    border-bottom: 1px solid #f1f5f9;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.doc-section-body {
    padding: 14px;
    background: #f8fafc;
}

.doc-list.vertical {
    display: flex;
    flex-direction: column;
    gap: 10px;
}

.doc-card {
    position: relative;
    width: 180px;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 10px;
    background: #fff;
    transition: all 0.2s ease;
}

.doc-card:hover {
    border-color: #cbd5e1;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04), 0 4px 12px rgba(0, 0, 0, 0.03);
}

.doc-page {
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.page-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #94a3b8;
    font-weight: 700;
    text-align: left;
    padding-left: 2px;
}

.doc-page-content {
    position: relative;
    height: 120px;
    border: 1px solid #e2e8f0;
    background: #f8fafc;
    border-radius: 6px;
    overflow: hidden;
}

.doc-page img {
    width: 100%;
    height: 100%;
    object-fit: contain;
}

.empty-page {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #cbd5e1;
    border: 2px dashed #e2e8f0;
    border-radius: 4px;
}

.icon-btn {
    border: none;
    background: transparent;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 12px;
    font-weight: 600;
    color: #4f46e5;
    padding: 4px 10px;
    border-radius: 6px;
    transition: background 0.15s ease;
}

.icon-btn:hover {
    background: #eef2ff;
}

.img-remove-overlay {
    position: absolute;
    top: 5px;
    right: 5px;
    background: rgba(239, 68, 68, 0.9);
    color: white;
    border: none;
    border-radius: 6px;
    width: 24px;
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transition: all 0.15s ease;
    cursor: pointer;
}

.img-remove-overlay:hover {
    background: rgba(220, 38, 38, 1);
}

.doc-page-content:hover .img-remove-overlay {
    opacity: 1;
}

.remove-page-icon {
    position: absolute;
    top: -7px;
    right: -7px;
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 50%;
    width: 22px;
    height: 22px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    color: #94a3b8;
    cursor: pointer;
    transition: all 0.15s ease;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
    z-index: 5;
}

.remove-page-icon:hover {
    background: #fef2f2;
    border-color: #fca5a5;
    color: #ef4444;
}
</style>
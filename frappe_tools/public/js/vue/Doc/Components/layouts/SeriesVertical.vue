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
/* BASE CONTAINER */
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

/* MAINTAINING YOUR VERTICAL LIST STRUCTURE */
.doc-list.vertical {
    display: flex;
    flex-direction: column;
    gap: 8px; /* Tighter gap to match styling */
}

/* ALIGNED CARD STYLING */
.doc-card {
    position: relative;
    width: 180px; /* Slightly wider for single vertical column */
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 8px;
    background: #fff;
    transition: box-shadow 0.2s;
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
    font-size: 9px;
    text-transform: uppercase;
    color: #94a3b8;
    font-weight: bold;
    text-align: left;
    padding-left: 2px;
}

.doc-page-content {
     position: relative;
    height: 120px;
    border: 1px solid #f1f5f9;
    background: #f8fafc;
    border-radius: 4px;
    overflow: hidden;
}

.doc-page img {
    width: 100%;
    height: 100%;
    object-fit: contain; /* Keeps image proportions */
}

.empty-page {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #cbd5e1;
    border: 2px dashed #e2e8f0;
}

/* ICON BUTTONS & OVERLAYS */
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
    z-index: 5;
}

.remove-page-icon:hover {
    background: #fee2e2;
    color: #ef4444;
}
</style>
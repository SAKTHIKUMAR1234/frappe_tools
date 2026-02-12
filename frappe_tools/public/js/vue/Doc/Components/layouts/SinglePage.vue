<template>
    <div class="doc-section-card">
        <div class="doc-section-header">
            <span>{{ title }}</span>
            <button
                v-if="!page"
                class="icon-btn primary"
                title="Add Layout"
                @click="onAddLayout"
            >
                <i class="pi octicon octicon-plus"></i> <span>Add Page</span>
            </button>
        </div>

        <div class="doc-section-body" v-if="page">
            <div class="doc-list single-page">
                <div class="doc-card">
                    <button class="remove-page-icon" title="Remove Page" @click="onRemovePage">
                        <i class="pi octicon octicon-x"></i>
                    </button>

                    <div class="doc-page front">
                        <div class="page-label">Page</div>
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

                            <button v-if="page.front" class="img-remove-overlay" @click="onRemoveImage">
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
    images: {
        type: Array,
        default: () => [],
    },
    title: String,
})

const emit = defineEmits([
    'addPage',
    'removePage',
    'removeImage',
])

const page = computed(() => {
    if (!props.images.length) return null

    const img = props.images[0]

    return {
        page_no: img.page_no,
        front: img.attachment || null,
    }
})

const onAddLayout = () => {
    emit('addPage', [
        {
            page_no: 1,
            page_type: 'Front',
            attachment: null,
        },
    ])
}

const onRemoveImage = () => {
    emit('removeImage', [page.value.page_no, 'Front'])
}

const onRemovePage = () => {
    emit('removePage', page.value.page_no)
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
    min-height: 45px;
}

.doc-section-body {
    padding: 14px;
    background: #f8fafc;
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
    text-align: center;
}

.doc-page-content {
    position: relative;
    height: 140px;
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
    z-index: 10;
}

.remove-page-icon:hover {
    background: #fef2f2;
    border-color: #fca5a5;
    color: #ef4444;
}
</style>
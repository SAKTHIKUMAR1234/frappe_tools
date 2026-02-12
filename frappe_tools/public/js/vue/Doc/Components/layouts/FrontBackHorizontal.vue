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
                <div
                    v-for="(page, index) in imagePairs"
                    :key="index"
                    class="doc-card"
                >
                    <div class="doc-page-row">
                        <div class="doc-page">
                            <div class="page-label">Front</div>
                            <div class="doc-page-content">
                                <img
                                    v-if="page.front"
                                    :src="page.front"
                                    :draggable="true"
                                    @dragstart.stop="dragHandler.dragStart(props.title, page.page_no, 'Front')"
                                    @dragend.stop="dragHandler.dragEnd()"
                                />
                                <div
                                    v-else
                                    class="empty-page"
                                    :draggable="true"
                                    @dragover.stop="dragHandler.dragHover(props.title, page.page_no, 'Front')"
                                >
                                   <i class="pi octicon octicon-download"></i>
                                </div>
                                
                                <button v-if="page.front" class="img-remove-overlay" @click="onRemoveImage(index, 'front')">
                                    <i class="pi octicon octicon-trash"></i>
                                </button>
                            </div>
                        </div>

                        <div class="doc-page">
                            <div class="page-label">Back</div>
                            <div class="doc-page-content">
                                <img
                                    v-if="page.back"
                                    :src="page.back"
                                    :draggable="true"
                                    @dragstart.stop="dragHandler.dragStart(props.title, page.page_no, 'Back')"
                                    @dragend.stop="dragHandler.dragEnd()"
                                />
                                <div
                                    v-else
                                    class="empty-page"
                                    :draggable="true"
                                    @dragover.stop="dragHandler.dragHover(props.title, page.page_no, 'Back')"
                                >
                                    <i class="pi octicon octicon-download"></i>
                                </div>

                                <button v-if="page.back" class="img-remove-overlay" @click="onRemoveImage(index, 'back')">
                                    <i class="pi octicon octicon-trash"></i>
                                </button>
                            </div>
                        </div>
                    </div>

                    <button class="remove-page-icon" title="Remove Page" @click="onRemovePage(index)">
                        <i class="pi octicon octicon-x"></i>
                    </button>
                </div>
            </div>
        </div>
    </div>
</template>

<script setup>
import { computed } from "vue";
import { useGloabalDragMemory } from "../../../Store/doc_scanner_drag_drop_memory";

const dragHandler = useGloabalDragMemory();

const props = defineProps({
    images: {
        type: Array,
        default: () => [],
    },
    title: String,
    is_new: Boolean,
});

const emit = defineEmits([
    "addPage",
    "removePage",
    "removeImage",
]);

const imagePairs = computed(() => {
    const map = {};

    props.images.forEach((img) => {
        if (!map[img.page_no]) {
            map[img.page_no] = {
                page_no: img.page_no,
                front: null,
                back: null,
            };
        }

        if (img.page_type === "Front") {
            map[img.page_no].front = img.attachment;
        }

        if (img.page_type === "Back") {
            map[img.page_no].back = img.attachment;
        }
    });

    return Object.values(map).sort(
        (a, b) => a.page_no - b.page_no
    );
});

const onAddLayout = () => {
    emit("addPage", [
        {
            page_no: imagePairs.value.length + 1,
            page_type: "Front",
            attachment: null,
        },
        {
            page_no: imagePairs.value.length + 1,
            page_type: "Back",
            attachment: null,
        },
    ]);
};

const onRemoveImage = (pageIndex, side) => {
    emit("removeImage", [
        imagePairs.value[pageIndex].page_no,
        side === "front" ? "Front" : "Back",
    ]);
};

const onRemovePage = (pageIndex) => {
    emit("removePage", imagePairs.value[pageIndex].page_no);
};
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

.doc-list {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
}

.doc-card {
    position: relative;
    width: 280px;
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

.doc-page-row {
    display: flex;
    gap: 8px;
}

.doc-page {
    flex: 1;
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
    height: 110px;
    border: 1px solid #e2e8f0;
    background: #f8fafc;
    border-radius: 6px;
    overflow: hidden;
}

.doc-page img {
    width: 100%;
    height: 100%;
    object-fit: cover;
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
}

.remove-page-icon:hover {
    background: #fef2f2;
    border-color: #fca5a5;
    color: #ef4444;
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
</style>
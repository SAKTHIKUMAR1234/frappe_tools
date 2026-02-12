<template>
  <div class="doc-section-card sidebar-mode">
    <div class="doc-section-header">
       <span>Unused Scans</span>
       <span class="count-badge">{{ images.length }}</span>
    </div>
    
    <div class="doc-section-body scrollable-area">
      <div class="image-row">
        <div v-for="(img, index) in images" :key="index" class="image-wrapper">
          <div class="doc-page-content clarity-container">
            <img 
              :src="img" 
              class="image-item" 
              :draggable="true" 
              @dragstart="onDragStart(index)" 
              @dragover="onDragOver(index)"
              @dragend="onDrop()" 
            />
            
            <button class="img-remove-overlay" @click="removeImage(index)">
               <i class="pi octicon octicon-trash"></i>
            </button>
          </div>
          <div class="page-label">Scan {{ index + 1 }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ── Sidebar Container ── */
.sidebar-mode {
  width: 250px;
  border: 1px solid #e2e8f0;
  background: #fff;
  height: calc(100vh - 120px);
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 20px;
  border-radius: 10px;
  overflow: hidden;
}

.doc-section-header {
  padding: 12px 16px;
  font-weight: 600;
  font-size: 13px;
  color: #1e293b;
  background: #fff;
  border-bottom: 1px solid #f1f5f9;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

/* ── Scrollable Area ── */
.scrollable-area {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  background: #f8fafc;
}

.image-row {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ── Image Card ── */
.clarity-container {
  position: relative;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  width: 100%;
  display: flex;
  justify-content: center;
  overflow: hidden;
  transition: box-shadow 0.2s ease;
}

.clarity-container:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.image-item {
  width: 100%;
  height: auto;
  display: block;
  object-fit: contain;
  image-rendering: -webkit-optimize-contrast;
  cursor: grab;
}

.page-label {
  font-size: 11px;
  color: #94a3b8;
  text-align: center;
  margin-top: 6px;
  font-weight: 600;
}

/* ── Scrollbar ── */
.scrollable-area::-webkit-scrollbar {
  width: 5px;
}
.scrollable-area::-webkit-scrollbar-track {
  background: transparent;
}
.scrollable-area::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 10px;
}
.scrollable-area::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

/* ── Remove Overlay ── */
.img-remove-overlay {
  position: absolute;
  top: 6px;
  right: 6px;
  background: rgba(239, 68, 68, 0.9);
  color: white;
  border: none;
  border-radius: 6px;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: all 0.15s ease;
  cursor: pointer;
  backdrop-filter: blur(4px);
}

.img-remove-overlay:hover {
  background: rgba(220, 38, 38, 1);
  transform: scale(1.05);
}

.clarity-container:hover .img-remove-overlay {
  opacity: 1;
}

.count-badge {
  background: #eef2ff;
  color: #4f46e5;
  padding: 2px 9px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
}
</style>

<script setup>
import { ref } from 'vue';
import { useGloabalDragMemory } from '../../Store/doc_scanner_drag_drop_memory';

const dragMem = useGloabalDragMemory();

const props = defineProps({
  images: {
    type: Array,
    required: true
  }
});

function onDragStart(index) {
  dragMem.dragStart('un_used_section', index, null);
}

function onDragOver(index) {
  dragMem.dragHover('un_used_section', index, null);
}

function onDrop() {
  dragMem.dragEnd();
}

const emit = defineEmits(['remove', 'update:images']);

function removeImage(index) {
  emit('remove', index);
}
</script>
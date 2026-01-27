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
/* MAIN SIDEBAR CONTAINER */
.sidebar-mode {
  width: 250px;
  border: 1px solid #e2e8f0;
  background: #fff;
  /* Key fix: Force sidebar to stay within the screen height */
  height: calc(100vh - 120px); 
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 20px;
}

.doc-section-header {
  padding: 10px 12px;
  font-weight: 600;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  justify-content: space-between;
}

/* THE SCROLL FIX */
.scrollable-area {
  flex: 1; /* Takes up all remaining space in the sidebar */
  overflow-y: auto; /* Enables scrolling ONLY inside this div */
  padding: 12px;
  background: #f1f5f9; /* Makes white pages stand out */
}

.image-row {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* CLARITY BOX */
.clarity-container {
  position: relative;
  background: #fff;
  border: 1px solid #cbd5e1;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
  /* We don't fix the height here, so the image can be tall and clear */
  width: 100%;
  display: flex;
  justify-content: center;
}

.image-item {
  width: 100%;
  height: auto;
  display: block;
  object-fit: contain; /* Shows full page */
  image-rendering: -webkit-optimize-contrast; /* Sharpens text */
  cursor: grab;
}

.page-label {
  font-size: 10px;
  color: #64748b;
  text-align: center;
  margin-top: 5px;
  font-weight: bold;
}

/* SCRL BAR STYLING */
.scrollable-area::-webkit-scrollbar {
  width: 6px;
}
.scrollable-area::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 10px;
}

/* OVERLAY */
.img-remove-overlay {
  position: absolute;
  top: 5px;
  right: 5px;
  background: rgba(239, 68, 68, 0.9);
  color: white;
  border: none;
  border-radius: 4px;
  width: 24px;
  height: 24px;
  opacity: 0;
  transition: opacity 0.2s;
  cursor: pointer;
}

.clarity-container:hover .img-remove-overlay {
  opacity: 1;
}

.count-badge {
    background: #e2e8f0;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
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
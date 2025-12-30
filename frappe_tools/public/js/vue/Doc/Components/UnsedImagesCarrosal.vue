<template>
  <div class="image-row">
    <div v-for="(img, index) in images" :key="index" class="image-wrapper">
      <img :src="img" class="image-item" :draggable="true" @dragstart="onDragStart(index)" @dragover="onDragOver(index)"
        @dragend="onDrop(index)" />
      <button class="delete-btn" @click="removeImage(index)" title="Delete image">
        ✕
      </button>
    </div>
  </div>
</template>

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

<style scoped>
.image-row {
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow-y: auto;
  width: 250px;
  max-height: 100vh;
  padding: 5px;
  position: sticky;
  top: 50px;
}

.image-row::-webkit-scrollbar {
  display: none;
}

.image-wrapper {
  position: relative;
}

.image-item {
  width: 100%;
  object-fit: contain;
  padding: 10px;
  background: #f5f5f5;
  border-radius: 4px;
}

.delete-btn {
  position: absolute;
  top: 6px;
  right: 6px;
  background: rgba(0, 0, 0, 0.7);
  color: #fff;
  border: none;
  border-radius: 50%;
  width: 22px;
  height: 22px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.2s;
}

/* Show on hover */
.image-wrapper:hover .delete-btn {
  opacity: 1;
}
</style>

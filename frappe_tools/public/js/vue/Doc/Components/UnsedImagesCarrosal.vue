<template>
  <div class="image-row">
    <div
      v-for="(img, index) in images"
      :key="index"
      class="image-wrapper"
    >
      <img :src="img" class="image-item" />

      <button
        class="delete-btn"
        @click="removeImage(index)"
        title="Delete image"
      >
        ✕
      </button>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  images: {
    type: Array,
    required: true
  }
});

const emit = defineEmits(['remove']);

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
  max-height: 90vh;
  padding: 5px;
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

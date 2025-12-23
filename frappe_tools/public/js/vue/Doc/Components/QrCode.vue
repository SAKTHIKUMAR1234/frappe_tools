<template>
  <div class="qr-wrapper">
    <canvas ref="qrCanvas"></canvas>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import QRCode from 'qrcode'
const props = defineProps(['properties']);
const qrCanvas = ref(null)
onMounted(() => {
  QRCode.toCanvas(
    qrCanvas.value,
    props.properties,
    {
      width: 200,
      margin: 2
    }
  )
})
</script>

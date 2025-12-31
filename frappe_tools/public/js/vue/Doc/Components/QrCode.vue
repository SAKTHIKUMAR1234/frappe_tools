<template>
    <div class="qr-wrapper">
        <canvas ref="qrCanvas"></canvas>
    </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import QRCode from 'qrcode'

const props = defineProps({
    properties: {
        type: String,
        default: '',
    },
})

const qrCanvas = ref(null)

/* ---------------- QR RENDER ---------------- */

function renderQrCode() {
    if (!qrCanvas.value || !props.properties) return

    QRCode.toCanvas(
        qrCanvas.value,
        props.properties,
        {
            width: 200,
            margin: 2,
            errorCorrectionLevel: 'M'
        }
    )
}

/* ---------------- WATCH ---------------- */

watch(
    () => props.properties,
    () => {
        renderQrCode()
    }
)

/* ---------------- MOUNT ---------------- */

onMounted(() => {
    renderQrCode()
})
</script>

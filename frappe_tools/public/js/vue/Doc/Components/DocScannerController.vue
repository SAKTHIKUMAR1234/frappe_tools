<template>
    <UnsedImagesCarrosal @remove="removeImage" :images="imagesList"></UnsedImagesCarrosal>
</template>
<script setup>
import { useSessionStore } from '../../Store/docscanner_store';
import { ref, onMounted, watch } from 'vue';
import UnsedImagesCarrosal from './UnsedImagesCarrosal.vue';

const sessionStore = useSessionStore();
const imagesList = ref([]);

const rtcConfig = {
    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
};

let pc = null;
let dataChannel = null;

const imageChunks = new Map();

const pendingCandidates = [];

function createPeer() {
    pc = new RTCPeerConnection(rtcConfig);

    dataChannel = pc.createDataChannel('scanner');

    dataChannel.onopen = () => {
        console.log('DataChannel OPEN');

        sendDataToFlutter({
            type: 'ping',
            message: 'Hello Flutter',
            timestamp: Date.now()
        });
    };

    dataChannel.onmessage = (e) => {
        try {
            const payload = JSON.parse(e.data);

            if (payload.type !== 'chunk') return;

            const { id, index, total, data } = payload;

            if (!imageChunks.has(id)) {
                imageChunks.set(id, {
                    total,
                    received: 0,
                    chunks: new Array(total)
                });
            }

            const imageData = imageChunks.get(id);

            if (!imageData.chunks[index]) {
                imageData.chunks[index] = data;
                imageData.received++;
            }

            if (imageData.received === imageData.total) {
                let base64 = imageData.chunks.join('');
                imageChunks.delete(id);
                base64 = JSON.parse(base64)['data'];
                imagesList.value = [
                    ...imagesList.value,
                    `data:image/jpeg;base64,${base64}`
                ];
            }
            console.log(`Received chunk ${index + 1}/${total} for image ${id}`);
        } catch (err) {
            console.error('Chunk handling error:', err);
        }
    };

    pc.onicecandidate = (event) => {
        if (!event.candidate) return;

        frappe.call({
            method: 'frappe_tools.api.doc_scanner.send_signal',
            args: {
                room: sessionStore.sessionId,
                device: 'web',
                signal_data: {
                    type: 'candidate',
                    candidate: event.candidate.toJSON()
                }
            }
        });
    };

    pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'disconnected' || pc.connectionState === 'failed') {
            sessionStore.disconnect();
        }
        if (pc.connectionState === 'connected') {
            sessionStore.connect();
        }
    };
}

function sendDataToFlutter(payload) {
    if (!dataChannel || dataChannel.readyState !== 'open') return;
    dataChannel.send(JSON.stringify(payload));
}

async function createOffer() {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    frappe.call({
        method: 'frappe_tools.api.doc_scanner.send_signal',
        args: {
            room: sessionStore.sessionId,
            device: 'web',
            signal_data: {
                type: 'offer',
                sdp: offer.sdp
            }
        }
    });
}

function removeImage(index) {
  imagesList.value.splice(index, 1);
}

async function handleSignals(signal) {
    try {
        if (signal.type === 'answer') {
            await pc.setRemoteDescription({
                type: 'answer',
                sdp: signal.sdp
            });

            for (const c of pendingCandidates) {
                await pc.addIceCandidate(c);
            }
            pendingCandidates.length = 0;
        }

        if (signal.type === 'candidate' && signal.candidate) {
            const candidate = new RTCIceCandidate(signal.candidate);

            if (pc.remoteDescription) {
                await pc.addIceCandidate(candidate);
            } else {
                pendingCandidates.push(candidate);
            }
        }
    } catch (e) {
        console.error('handleSignals error:', e);
    }
}


watch(imagesList, () => {
    console.log('Images list updated:', imagesList.value.length);
});

onMounted(async () => {
    createPeer();
    await createOffer();
});

defineExpose({ handleSignals });
</script>

<style scoped>
/* Page */
.scanner-page {
    height: 100vh;
    display: flex;
    align-items: flex-end;
    background: #f3f4f6;
}

/* Carousel container */
.carousel-container {
    width: 100%;
    padding: 12px;
    background: #ffffff;
    border-top: 1px solid #e5e7eb;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
}

/* Track */
.carousel-track {
    display: flex;
    gap: 14px;
    scroll-snap-type: x mandatory;
}

/* Each item */
.carousel-item {
    flex: 0 0 auto;
    width: 140px;
    height: 190px;
    scroll-snap-align: start;
    animation: slideUpFade 0.35s ease-out both;
}

/* Image card */
.image-wrapper {
    width: 100%;
    height: 100%;
    border-radius: 10px;
    overflow: hidden;
    background: #fff;
    box-shadow: 0 8px 20px rgba(0, 0, 0, 0.12);
    transform: translateZ(0);
}

/* Image */
.image-wrapper img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

/* Animation */
@keyframes slideUpFade {
    from {
        opacity: 0;
        transform: translateY(12px) scale(0.96);
    }

    to {
        opacity: 1;
        transform: translateY(0) scale(1);
    }
}
</style>
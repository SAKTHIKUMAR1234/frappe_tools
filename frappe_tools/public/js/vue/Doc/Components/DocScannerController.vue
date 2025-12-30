<template>
    <div class="main_container" v-if="dragStore">
        <UnsedImagesCarrosal class="width : 15%" :images="dragStore.imagesList"
            @remove="removeImage" @update:images="val => imagesList = val" />
        <MainLayoutHandler class="width : 85%" :is_new="props.is_new" :document_name="props.document_name"
            :scan_name="props.scan_name"
            :doctype="props.doctype" />
    </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { useSessionStore } from '../../Store/docscanner_store'
import { useGloabalDragMemory } from '../../Store/doc_scanner_drag_drop_memory';

import UnsedImagesCarrosal from './UnsedImagesCarrosal.vue'
import MainLayoutHandler from './layouts/MainLayoutHandler.vue'

const sessionStore = useSessionStore();
const dragStore = useGloabalDragMemory();

const props = defineProps({
    is_new: { type: Boolean, default: true },
    document_name: { type: String, default: null },
    doctype: { type: String, default: null },
    scan_name: { type: String, default: null }
})

const emit = defineEmits(['reload_session']);


// const imagesList = ref(dragStore.imagesList);

let pc = null
let dataChannel = null

const pendingCandidates = []
const imageChunks = new Map()


const rtcConfig = {
    iceServers: [{
        urls: [
            'stun:stun.l.google.com:19302',
            'stun:stun1.l.google.com:19302',
            'stun:stun2.l.google.com:19302',
            'stun:stun3.l.google.com:19302',
            'stun:stun4.l.google.com:19302',
        ]
    }]
}


function destroyPeer() {
    try {
        if (dataChannel) {
            dataChannel.onopen = null
            dataChannel.onmessage = null
            dataChannel.close()
        }

        if (pc) {
            pc.onicecandidate = null
            pc.onconnectionstatechange = null
            pc.close()
        }
    } catch (e) {
        console.warn('Peer cleanup error:', e)
    }

    pc = null
    dataChannel = null
    pendingCandidates.length = 0
    imageChunks.clear()
}


function createPeer() {
    destroyPeer()

    pc = new RTCPeerConnection(rtcConfig)

    dataChannel = pc.createDataChannel('scanner')

    dataChannel.onopen = () => {
        console.log('DataChannel OPEN')
    }

    dataChannel.onmessage = handleDataMessage

    pc.onicecandidate = handleIceCandidate

    pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'connected') {
            sessionStore.connect()
        }

        if (
            pc.connectionState === 'disconnected' ||
            pc.connectionState === 'closed'
        ) {
            sessionStore.disconnect()
            destroyPeer()
            emit('reload_session');
        }
    }
}


function handleDataMessage(e) {
    try {
        const payload = JSON.parse(e.data)
        if (payload.type !== 'chunk') return

        const { id, index, total, data } = payload

        if (!imageChunks.has(id)) {
            imageChunks.set(id, {
                total,
                received: 0,
                chunks: new Array(total)
            })
        }

        const imageData = imageChunks.get(id)

        if (!imageData.chunks[index]) {
            imageData.chunks[index] = data
            imageData.received++
        }

        if (imageData.received === imageData.total) {
            let base64 = imageData.chunks.join('')
            imageChunks.delete(id)

            base64 = JSON.parse(base64).data

            dragStore.setImagesDetails(
                [
                    ...dragStore.imagesList,
                    `data:image/jpeg;base64,${base64}`
                ]
            )
        }
    } catch (err) {
        console.error('Chunk handling error:', err)
    }
}

function handleIceCandidate(event) {
    if (!event.candidate) return

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
    })
}

async function createOffer() {
    if (!pc || pc.signalingState === 'closed') return

    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

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
    })
}

async function handleSignals(signal) {
    try {
        if (!pc) return

        if (signal.type === 'answer') {
            await pc.setRemoteDescription({
                type: 'answer',
                sdp: signal.sdp
            })

            for (const c of pendingCandidates) {
                await pc.addIceCandidate(c)
            }
            pendingCandidates.length = 0
        }

        if (signal.type === 'candidate' && signal.candidate) {
            const candidate = new RTCIceCandidate(signal.candidate)

            if (pc.remoteDescription) {
                await pc.addIceCandidate(candidate)
            } else {
                pendingCandidates.push(candidate)
            }
        }
    } catch (e) {
        console.error('handleSignals error:', e)
    }
}


function removeImage(index) {
    let s = dragStore.imagesList.filter((_, i) => i !== index)
    dragStore.setImagesDetails(s);
}

watch(
    () => sessionStore.status,
    async (status) => {
        if (status === 'connecting') {
            createPeer()
            await createOffer()
        }

        if (status === 'disconnected') {
            destroyPeer()
        }
    }
)

watch(
    () => dragStore.imagesList, () => {
        console.log(dragStore.imagesList);
    }
)

onMounted(async () => {
    createPeer()
    await createOffer()
})

onBeforeUnmount(() => {
    destroyPeer()
})


defineExpose({ handleSignals })
</script>

<style scoped>
.main_container {
    display: flex;
    flex-direction: row;
    gap: 10px;
    width: 100%;
}
</style>

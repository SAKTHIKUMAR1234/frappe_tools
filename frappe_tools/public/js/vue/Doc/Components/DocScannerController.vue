<template>
    <div class="root-container" v-if="dragStore">
        <!-- Connection Status / PIN Section -->
        <div class="connection-bar">
            <div v-if="sessionStore.status === 'connected'" class="status-connected">
                <span class="indicator online"></span> Mobile Connected
                <button class="btn btn-primary btn-xs link-btn" @click="triggerMobileScanner">
                    Scan Now
                </button>
                <button class="btn btn-danger btn-xs link-btn" @click="manualDisconnect">
                    Disconnect
                </button>
            </div>

            <div v-else class="status-disconnected">
                <div class="pin-section" v-if="!sessionStore.isSetupMode">
                    <span class="label">Mobile Connection PIN:</span>
                    <span class="pin-code-small">{{ sessionStore.pin || '...' }}</span>
                    <button class="btn btn-default btn-xs link-btn" @click="sessionStore.toggleSetupMode">
                        Setup App
                    </button>
                    <button class="btn btn-default btn-xs link-btn" @click="sessionStore.generatePin(true)">
                        &#x21bb;
                    </button>
                </div>
                <div class="setup-section" v-else>
                    <span class="label">Scan to Setup:</span>
                    <canvas ref="qrcodeCanvas" class="qr-canvas-small"></canvas>
                    <button class="btn btn-default btn-xs" @click="sessionStore.toggleSetupMode">
                        Close Setup
                    </button>
                </div>
            </div>
        </div>

        <!-- Main Content (Always Visible) -->
        <div class="main_container">
            <UnsedImagesCarrosal style="width : 60%" :images="dragStore.imagesList" @remove="removeImage"
                @update:images="val => imagesList = val" />
            <MainLayoutHandler style="width : 40%" :is_new="localIsNew" :document_name="localDocname"
                :scan_name="props.scan_name" :doctype="localDoctype" @newScan="startNewScan"
                @created="localIsNew = false" />
        </div>
    </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useSessionStore } from '../../Store/docscanner_store'
import { useGloabalDragMemory } from '../../Store/doc_scanner_drag_drop_memory';
import QRCode from 'qrcode';
import { v4 as uuidv4 } from 'uuid';

import UnsedImagesCarrosal from './UnsedImagesCarrosal.vue'
import MainLayoutHandler from './layouts/MainLayoutHandler.vue'

const sessionStore = useSessionStore();
const dragStore = useGloabalDragMemory();
const qrcodeCanvas = ref(null);
const origin = window.location.origin;

const props = defineProps({
    is_new: { type: Boolean, default: true },
    document_name: { type: String, default: null },
    doctype: { type: String, default: null },
    scan_name: { type: String, default: null }
})

const localDoctype = ref(props.doctype);
const localDocname = ref(props.document_name);
const localIsNew = ref(props.is_new);
const allowedDoctypes = ref([]);

watch(() => props.doctype, (val) => {
    if (val) localDoctype.value = val;
});

watch(() => props.document_name, (val) => {
    if (val) localDocname.value = val;
});

watch(() => props.is_new, (val) => {
    localIsNew.value = val;
});

const emit = defineEmits(['reload_session']);


let pc = null
let dataChannel = null

const pendingCandidates = []
const imageChunks = new Map()


const iceServers = ref([])

async function fetchIceServers() {
    return new Promise((resolve, reject) => {
        frappe.call({
            method: 'frappe_tools.api.doc_scanner.get_ice_servers',
            callback: (r) => {
                iceServers.value = r.message || []
                resolve(iceServers.value)
            },
            error: (err) => reject(err)
        })
    })
}


function playBeep() {
    try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const oscillator = audioCtx.createOscillator();
        const gainNode = audioCtx.createGain();

        oscillator.connect(gainNode);
        gainNode.connect(audioCtx.destination);

        oscillator.type = 'sine';
        oscillator.frequency.setValueAtTime(880, audioCtx.currentTime); // A5
        gainNode.gain.setValueAtTime(0.1, audioCtx.currentTime);

        oscillator.start();
        oscillator.stop(audioCtx.currentTime + 0.1);
    } catch (e) {
        console.warn('Could not play beep:', e);
    }
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

function triggerMobileScanner() {
    if (dataChannel && dataChannel.readyState === 'open') {
        console.log('Sending open_scanner signal to mobile');
        dataChannel.send(JSON.stringify({ type: 'camera', message: 'open_camera' }));
    } else {
        frappe.call({
            method: 'frappe_tools.api.doc_scanner.send_signal',
            args: {
                room: sessionStore.sessionId,
                device: 'web',
                signal_data: { type: 'camera', message: 'open_camera' }
            }
        });
    }
}


async function manualDisconnect() {
    if (sessionStore.sessionId) {
        frappe.call({
            method: 'frappe_tools.api.doc_scanner.remove_scanner',
            args: { room: sessionStore.sessionId }
        });
    }
    sessionStore.disconnect();
    await sessionStore.generatePin();
}


function createPeer() {
    destroyPeer()

    pc = new RTCPeerConnection({
        iceServers: iceServers.value
    })

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
            pc.connectionState === 'failed' ||
            pc.connectionState === 'closed'
        ) {
            sessionStore.disconnect()
            destroyPeer()
            emit('reload_session');
            sessionStore.generatePin();
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
            playBeep();
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
            // if (pc.signalingState === 'stable') {
            //     console.warn('Received answer but signaling state is already stable. Ignoring.');
            //     return;
            // }
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

watch(() => sessionStore.isSetupMode, async (val) => {
    if (val) {
        await nextTick();
        if (qrcodeCanvas.value) {
            QRCode.toCanvas(qrcodeCanvas.value, JSON.stringify({
                server_url: window.location.origin
            }), { width: 300 }, function (error) { // Increased size to 300
                if (error) console.error(error)
            })
        }
    }
})

const realtimeHandler = (data) => {
    if (data.device_type !== 'mobile') return

    if (data.event === 'scanner_added') {
        sessionStore.connecting()
    }
    else if (data.event === 'scanner_removed') {
        sessionStore.disconnect()
    }
    else if (data.event === 'signal') {
        handleSignals(data.data)
    }
};

let currentListeningRoom = null;

function updateRealtimeListener(roomName) {
    if (currentListeningRoom) {
        frappe.realtime.off(currentListeningRoom, realtimeHandler);
    }
    if (roomName) {
        frappe.realtime.on(roomName, realtimeHandler);
        currentListeningRoom = roomName;
    }
}

watch(() => sessionStore.sessionId, (newId) => {
    updateRealtimeListener(newId);
}, { immediate: true });

const fetchAllowedDoctypes = async () => {
    const r = await frappe.call('frappe_tools.api.doc_scanner.get_docscanner_allowed_doctypes');
    allowedDoctypes.value = r.message || [];
}

const checkAndPromptDocInfo = () => {
    if (!localDoctype.value || !localDocname.value) {
        frappe.prompt([
            {
                label: 'DocType',
                fieldname: 'doctype',
                fieldtype: 'Link',
                options: 'DocType',
                reqd: 1,
                get_query: () => {
                    return {
                        filters: [
                            ['name', 'in', allowedDoctypes.value]
                        ]
                    }
                }
            },
            {
                label: 'Document Name',
                fieldname: 'docname',
                fieldtype: 'Dynamic Link',
                options: 'doctype',
                reqd: 1
            }
        ], (values) => {
            localDoctype.value = values.doctype;
            localDocname.value = values.docname;
        }, 'Set Document Details', 'Save');
    }
}

const startNewScan = () => {
    localDoctype.value = null;
    localDocname.value = null;
    localIsNew.value = true;
    dragStore.clearAll();

    // Update URL to 'new' without reload
    const newPath = `document-scanner/new`;
    window.history.replaceState(null, '', `/app/${newPath}`);

    checkAndPromptDocInfo();
}

onMounted(async () => {
    // Force a fresh session and room on every reload/mount
    sessionStore.resetSession();

    // Explicitly notify backend about the new session room
    sessionStore.createSession(sessionStore.sessionId);

    await sessionStore.generatePin();
    await fetchIceServers();
    await fetchAllowedDoctypes();
    checkAndPromptDocInfo();
})

onBeforeUnmount(() => {
    updateRealtimeListener(null);
    destroyPeer()
})


defineExpose({ handleSignals })
</script>

<style scoped>
.root-container {
    display: flex;
    flex-direction: column;
    gap: 15px;
    width: 100%;
}

.connection-bar {
    background: var(--card-bg);
    /* Match card style */
    border: 1px solid var(--border-color);
    padding: 10px 15px;
    border-radius: 6px;
    display: flex;
    justify-content: center;
    align-items: center;
    position: sticky;
    top: 0;
    z-index: 1000000;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
}

.status-connected {
    color: var(--text-color);
    font-weight: 500;
}

.indicator {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 6px;
}

.indicator.online {
    background-color: var(--green-500);
}

.status-disconnected {
    width: 100%;
    display: flex;
    justify-content: center;
}

.pin-section,
.setup-section {
    display: flex;
    align-items: center;
    gap: 12px;
}

.label {
    color: var(--text-muted);
    font-size: 0.9em;
}

.pin-code-small {
    font-size: 1.2rem;
    font-weight: 700;
    font-family: monospace;
    letter-spacing: 2px;
    background: var(--bg-light-gray);
    padding: 2px 8px;
    border-radius: 4px;
}

.link-btn {
    margin-left: 5px;
}

.qr-canvas-small {
    height: 100px !important;
    width: 100px !important;
}

.main_container {
    display: flex;
    flex-direction: row;
    gap: 10px;
    width: 100%;
}
</style>

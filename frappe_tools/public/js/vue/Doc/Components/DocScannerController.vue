<template>
    <div class="root-container" v-if="dragStore">
        <div class="connection-bar">
            <div v-if="sessionStore.status === 'connected'" class="status-connected">
                <span class="indicator online"></span> Mobile Connected
                <button class="btn btn-primary btn-xs link-btn" :disabled="isScanning" @click="triggerMobileScanner">
                    <span v-if="isScanning">
                        <i class="fa fa-spinner fa-spin"></i> Scanning…
                    </span>
                    <span v-else>
                        Scan Now
                    </span>
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

        <div v-if="scanMode === 'template'" class="template-mode-wrapper">
            <div class="doc-info-bar">
                <div class="doc-info-bar-left">
                    <div class="doc-info-bar-icon">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="3" y="3" width="7" height="7"/>
                            <rect x="14" y="3" width="7" height="7"/>
                            <rect x="14" y="14" width="7" height="7"/>
                            <rect x="3" y="14" width="7" height="7"/>
                        </svg>
                    </div>
                    <span class="doc-info-bar-label">Template Scanning</span>
                    <span class="doc-info-bar-sep">&middot;</span>
                    <span class="doc-info-bar-value">{{ localDoctype }}</span>
                    <span class="doc-info-bar-sep">&middot;</span>
                    <span class="doc-info-bar-value">{{ localDocname }}</span>
                </div>
                <button class="doc-info-bar-btn" @click="startNewScan">
                    Change Document
                </button>
            </div>
            <div class="main_container">
                <UnsedImagesCarrosal style="width : 60%" :images="dragStore.imagesList" @remove="removeImage"
                    @update:images="val => imagesList = val" />
                <MainLayoutHandler style="width : 40%" :is_new="localIsNew" :document_name="localDocname"
                    :scan_name="props.scan_name" :doctype="localDoctype" @newScan="startNewScan"
                    @created="localIsNew = false" />
                <DocumentListViewer v-if="localDocname && localDoctype"
                    :docname="localDocname" :doctype="localDoctype" />
            </div>
        </div>

        <div v-else-if="scanMode === 'direct'" class="main_container direct-mode">
            <DirectAttachmentPanel :doctype="localDoctype" :docname="localDocname" @changeDoc="startNewScan" @triggerScan="triggerMobileScanner" />
        </div>

        <div v-else class="main_container empty-mode">
            <div class="empty-mode-content">
                <div class="empty-mode-icon">
                    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                        <circle cx="12" cy="13" r="4"/>
                    </svg>
                </div>
                <h4>Ready to Scan</h4>
                <p>Select a document and scan mode to begin.</p>
            </div>
        </div>
    </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useSessionStore } from '../../Store/docscanner_store'
import { useGloabalDragMemory } from '../../Store/doc_scanner_drag_drop_memory';
import QRCode from 'qrcode';

import UnsedImagesCarrosal from './UnsedImagesCarrosal.vue'
import MainLayoutHandler from './layouts/MainLayoutHandler.vue'
import DocumentListViewer from '../Pages/DocumentListViewer.vue';
import DirectAttachmentPanel from './DirectAttachmentPanel.vue';

const sessionStore = useSessionStore();
const dragStore = useGloabalDragMemory();
const qrcodeCanvas = ref(null);
const origin = window.location.origin;
const isScanning = ref(false);
const scanMode = ref(null);

const props = defineProps({
    is_new: { type: Boolean, default: true },
    document_name: { type: String, default: null },
    doctype: { type: String, default: null },
    scan_name: { type: String, default: null }
})

const imageReceiveTimers = new Map();
const RECEIVE_TIMEOUT_MS = 15000;

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
    if (isScanning.value) return;
    isScanning.value = true;
    setTimeout(() => {
        isScanning.value = false;
    }, 2000);
    showScannerTriggered();
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
        const payload = JSON.parse(e.data);
        if (payload.type !== 'chunk') return;

        const { id, index, total, data } = payload;

        if (!imageChunks.has(id)) {
            imageChunks.set(id, {
                total,
                received: 0,
                chunks: new Array(total)
            });

            notifyStart(id);
            startReceiveTimeout(id);
        }

        const imageData = imageChunks.get(id);

        // Ignore duplicate chunks
        if (!imageData.chunks[index]) {
            imageData.chunks[index] = data;
            imageData.received++;
        }

        // COMPLETED
        if (imageData.received === imageData.total) {
            clearReceiveTimeout(id);

            let base64 = imageData.chunks.join('');
            imageChunks.delete(id);

            base64 = JSON.parse(base64).data;

            dragStore.setImagesDetails([
                ...dragStore.imagesList,
                `data:image/jpeg;base64,${base64}`
            ]);

            notifySuccess();
            playBeep();
        }

    } catch (err) {
        console.error('Chunk handling error:', err);
        notifyFailure();
    }
}


function startReceiveTimeout(id) {
    const timer = setTimeout(() => {
        imageChunks.delete(id);
        imageReceiveTimers.delete(id);
        notifyFailure();
    }, RECEIVE_TIMEOUT_MS);


    imageReceiveTimers.set(id, timer);
}


function clearReceiveTimeout(id) {
    const timer = imageReceiveTimers.get(id);
    if (timer) {
        clearTimeout(timer);
        imageReceiveTimers.delete(id);
    }
}


function notifyStart(id) {
    frappe.show_alert(
        { message: __('Receiving image…'), indicator: 'orange' },
        5
    );
}


function notifySuccess() {
    frappe.show_alert(
        { message: __('Image received successfully'), indicator: 'green' },
        5
    );
}

function showScannerTriggered() {
    frappe.show_alert(
        { message: __('Scanner Openned'), indicator: 'green' },
        5
    );
}


function notifyFailure() {
    frappe.show_alert(
        { message: __('Image failed to receive'), indicator: 'red' },
        7
    );
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

    const d = new frappe.ui.Dialog({
        title: 'Set Document Details',
        fields: [
            {
                label: 'DocType',
                fieldname: 'doctype',
                fieldtype: 'Link',
                options: 'DocType',
                reqd: 1,
                get_query: () => ({
                    filters: [['name', 'in', allowedDoctypes.value]]
                }),
                change() {
                    // Reset docname
                    d.set_value('docname', null);

                    // Enable docname
                    const docnameField = d.get_field('docname');
                    docnameField.df.read_only = 0;
                    docnameField.refresh();

                    // Clear table
                    clear_details_table();
                }
            },
            {
                label: 'Document Name',
                fieldname: 'docname',
                fieldtype: 'Dynamic Link',
                options: 'doctype',
                read_only: 1,
                change() {
                    const values = d.get_values();
                    if (values?.doctype && values?.docname) {
                        load_document_details(values.doctype, values.docname);
                    }
                },
                get_query: () => {
                    const values = d.get_values();
                    if (!values?.doctype) return {};
                    return {
                        query: 'frappe_tools.api.doc_scanner.get_doctype_filtered_values',
                    };
                }
            },
            {
                label: 'Document Details',
                fieldname: 'document_details',
                fieldtype: 'HTML'
            }
        ],
        primary_action_label: 'Save',
        static: true,
        primary_action(values) {
            if (!values.doctype || !values.docname) {
                frappe.msgprint(__('Please select both DocType and Document Name'));
                return;
            }

            localDoctype.value = values.doctype;
            localDocname.value = values.docname;
            d.hide();
            showModeSelection();
        }
    });

    function clear_details_table() {
        const htmlField = d.get_field('document_details');
        htmlField.$wrapper.html('');
    }

    function load_document_details(doctype, docname) {
        frappe.call({
            method: 'frappe_tools.api.doc_scanner.get_document_details',
            args: {
                doctype,
                docname
            },
            callback(r) {
                if (!r.message) return;

                const { fields, values } = r.message;

                build_details_table(fields, values);
            }
        });
    }

    function build_details_table(fields, values) {
        const htmlField = d.get_field('document_details');

        let html = `
        <div class="doc-details-table">
            <table class="table table-bordered table-sm">
                <tbody>
    `;

        fields.forEach(fieldname => {
            const label = frappe.model.unscrub(fieldname);
            const value = frappe.utils.escape_html(values[fieldname] ?? '');

            html += `
            <tr>
                <td style="width:40%; font-weight:600;">${label}</td>
                <td>${value}</td>
            </tr>
        `;
        });

        html += `
                </tbody>
            </table>
        </div>
    `;

        htmlField.$wrapper.html(html);
    }
    d.show();
}

function showModeSelection() {
    const md = new frappe.ui.Dialog({
        title: 'Select Scan Mode',
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'mode_cards',
            }
        ],
        static: true,
    });

    md.$wrapper.find('.modal-footer').hide();

    const html = `
        <div class="ms-container">
            <p class="ms-desc">How would you like to scan documents for this record?</p>

            <div class="ms-cards">
                <div class="ms-card" data-mode="template">
                    <div class="ms-card-icon ms-card-icon--tpl">
                        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="3" y="3" width="7" height="7"/>
                            <rect x="14" y="3" width="7" height="7"/>
                            <rect x="14" y="14" width="7" height="7"/>
                            <rect x="3" y="14" width="7" height="7"/>
                        </svg>
                    </div>
                    <div class="ms-card-body">
                        <h5 class="ms-card-title">Template Scanning</h5>
                        <p class="ms-card-text">Use scanner layouts to organize scanned pages into structured documents</p>
                    </div>
                    <div class="ms-card-arrow">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="9 18 15 12 9 6"/>
                        </svg>
                    </div>
                </div>

                <div class="ms-card" data-mode="direct">
                    <div class="ms-card-icon ms-card-icon--dir">
                        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                        </svg>
                    </div>
                    <div class="ms-card-body">
                        <h5 class="ms-card-title">Direct Attachment</h5>
                        <p class="ms-card-text">Scan images and attach directly to fields on the selected document</p>
                    </div>
                    <div class="ms-card-arrow">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="9 18 15 12 9 6"/>
                        </svg>
                    </div>
                </div>
            </div>
        </div>

        <style>
            .ms-container {
                padding: 4px 0 8px;
            }
            .ms-desc {
                margin: 0 0 18px;
                font-size: 13px;
                color: #64748b;
            }
            .ms-cards {
                display: flex;
                flex-direction: column;
                gap: 10px;
            }
            .ms-card {
                display: flex;
                align-items: center;
                gap: 16px;
                padding: 16px 18px;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                cursor: pointer;
                transition: all 0.2s ease;
                background: #fff;
            }
            .ms-card:hover {
                border-color: #cbd5e1;
                box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.03);
            }
            .ms-card:hover .ms-card-arrow {
                opacity: 1;
                transform: translateX(2px);
            }
            .ms-card:active {
                transform: scale(0.99);
            }
            .ms-card-icon {
                width: 48px;
                height: 48px;
                border-radius: 12px;
                display: flex;
                align-items: center;
                justify-content: center;
                flex-shrink: 0;
            }
            .ms-card-icon--tpl {
                background: #fef3c7;
                color: #d97706;
            }
            .ms-card-icon--dir {
                background: #eef2ff;
                color: #4f46e5;
            }
            .ms-card-body {
                flex: 1;
                min-width: 0;
            }
            .ms-card-title {
                margin: 0 0 3px;
                font-size: 14px;
                font-weight: 650;
                color: #1e293b;
            }
            .ms-card-text {
                margin: 0;
                font-size: 12.5px;
                color: #94a3b8;
                line-height: 1.5;
            }
            .ms-card-arrow {
                color: #cbd5e1;
                opacity: 0;
                transition: all 0.2s ease;
                flex-shrink: 0;
            }
        </style>
    `;

    md.get_field('mode_cards').$wrapper.html(html);

    md.$wrapper.find('.ms-card').on('click', function () {
        const mode = $(this).data('mode');
        scanMode.value = mode;
        md.hide();
    });

    md.show();
}

const startNewScan = () => {
    localDoctype.value = null;
    localDocname.value = null;
    localIsNew.value = true;
    scanMode.value = null;
    dragStore.clearAll();

    const newPath = `document-scanner/new`;
    window.history.replaceState(null, '', `/app/${newPath}`);

    checkAndPromptDocInfo();
}

const handleCtrlS = (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "s") {
        event.preventDefault();
        triggerMobileScanner();
    }
};

onMounted(async () => {
    sessionStore.resetSession();
    window.addEventListener("keydown", handleCtrlS);
    sessionStore.createSession(sessionStore.sessionId);

    await sessionStore.generatePin();
    await fetchIceServers();
    await fetchAllowedDoctypes();
    if(!localDoctype.value || !localDocname.value) {
        checkAndPromptDocInfo();
    } else if (localIsNew.value) {
        // New scan with known doctype/docname — let user pick mode
        showModeSelection();
    } else {
        // Existing scan loaded via URL — go straight to template mode
        scanMode.value = 'template';
    }
})

onBeforeUnmount(() => {
    window.removeEventListener("keydown", handleCtrlS);
    updateRealtimeListener(null);
    destroyPeer()
})


defineExpose({ handleSignals })
</script>

<style scoped>
.root-container {
    display: flex;
    flex-direction: column;
    gap: 16px;
    width: 100%;
}

/* ── Connection Bar ── */
.connection-bar {
    background: #fff;
    border: 1px solid #e2e8f0;
    padding: 12px 20px;
    border-radius: 10px;
    display: flex;
    justify-content: center;
    align-items: center;
    position: sticky;
    top: 0;
    z-index: 1049;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.status-connected {
    display: flex;
    align-items: center;
    gap: 10px;
    color: #1e293b;
    font-size: 13px;
    font-weight: 500;
}

.indicator {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 2px;
}

.indicator.online {
    background-color: #10b981;
    box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.15);
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
    gap: 14px;
}

.label {
    color: #64748b;
    font-size: 13px;
    font-weight: 500;
}

.pin-code-small {
    font-size: 1.3rem;
    font-weight: 700;
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    letter-spacing: 4px;
    background: #f1f5f9;
    color: #1e293b;
    padding: 4px 14px;
    border-radius: 8px;
    border: 1px solid #e2e8f0;
}

.link-btn {
    margin-left: 4px;
    border-radius: 6px !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    padding: 4px 12px !important;
    border: 1px solid #e2e8f0 !important;
    transition: all 0.15s ease;
}

.link-btn.btn-primary {
    background: #4f46e5 !important;
    border-color: #4f46e5 !important;
    color: #fff !important;
}

.link-btn.btn-primary:hover:not(:disabled) {
    background: #4338ca !important;
}

.link-btn.btn-danger {
    background: transparent !important;
    border-color: #fca5a5 !important;
    color: #dc2626 !important;
}

.link-btn.btn-danger:hover {
    background: #fef2f2 !important;
}

.link-btn.btn-default {
    background: #fff !important;
    color: #475569 !important;
}

.link-btn.btn-default:hover {
    background: #f8fafc !important;
    border-color: #cbd5e1 !important;
}

.qr-canvas-small {
    height: 100px !important;
    width: 100px !important;
    border-radius: 8px;
}

/* ── Template Mode Wrapper ── */
.template-mode-wrapper {
    display: flex;
    flex-direction: column;
    gap: 12px;
    width: 100%;
}

.doc-info-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 18px;
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
}

.doc-info-bar-left {
    display: flex;
    align-items: center;
    gap: 8px;
}

.doc-info-bar-icon {
    width: 30px;
    height: 30px;
    border-radius: 8px;
    background: #fef3c7;
    color: #d97706;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
}

.doc-info-bar-label {
    font-size: 13px;
    font-weight: 600;
    color: #1e293b;
}

.doc-info-bar-sep {
    color: #cbd5e1;
    font-size: 14px;
}

.doc-info-bar-value {
    font-size: 13px;
    color: #64748b;
    font-weight: 500;
}

.doc-info-bar-btn {
    display: inline-flex;
    align-items: center;
    height: 30px;
    padding: 0 14px;
    font-size: 12px;
    font-weight: 600;
    border-radius: 6px;
    border: 1px solid #e2e8f0;
    background: #fff;
    color: #475569;
    cursor: pointer;
    transition: all 0.15s ease;
    white-space: nowrap;
}

.doc-info-bar-btn:hover {
    background: #f8fafc;
    border-color: #cbd5e1;
}

/* ── Main Container ── */
.main_container {
    display: flex;
    flex-direction: row;
    gap: 12px;
    width: 100%;
}

.main_container.direct-mode {
    flex-direction: column;
}

.main_container.empty-mode {
    justify-content: center;
    align-items: center;
    min-height: calc(100vh - 200px);
}

.empty-mode-content {
    text-align: center;
    padding: 48px 24px;
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    max-width: 360px;
}

.empty-mode-icon {
    color: #cbd5e1;
    margin-bottom: 18px;
}

.empty-mode-content h4 {
    margin: 0 0 8px;
    font-size: 16px;
    font-weight: 650;
    color: #1e293b;
}

.empty-mode-content p {
    margin: 0;
    font-size: 13px;
    color: #94a3b8;
    line-height: 1.5;
}
</style>

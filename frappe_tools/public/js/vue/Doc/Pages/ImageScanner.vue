<template>
    <div class="container h-100 mt-2 d-flex flex-column">

        <div class="d-flex justify-content-end p-10" >
            <SessionIndicator />
        </div>

        <div class="mt-2 d-flex justify-content-center flex-column align-items-center"
            v-if="sessionStore.status !== 'connected'">
            <QrCode v-if="qrProperties" :properties="qrProperties" />

            <div class="mt-1">
                <small>
                    Scan this QR code with your mobile device to connect to the document scanner session.
                </small>
            </div>

            <button class="btn btn-outline-secondary btn-sm mt-2" :disabled="sessionStore.status === 'connecting'"
                @click="createNewSession">
                Refresh Room
            </button>
        </div>

        <div class="d-flex justify-content-center mt-2" v-if="props.is_new && sessionStore.status === 'connected'">
            <button class="btn btn-outline-danger btn-sm" @click="createNewSession">
                Disconnect & Create New Session
            </button>
        </div>

        <DocScannerController v-if="qrProperties" ref="controllerRef" :is_new="props.is_new" :doctype="props.doctype"
            :scan_name="props.scan_name" :document_name="props.document_name"
            @reload_session="() => createNewSession()" />
    </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { v4 as uuidv4 } from 'uuid'

import SessionIndicator from '../Components/SessionIndicator.vue'
import QrCode from '../Components/QrCode.vue'
import DocScannerController from '../Components/DocScannerController.vue'

import { useSessionStore } from '../../Store/docscanner_store'

const props = defineProps({
    is_new: {
        type: Boolean,
        default: true,
    },
    document_name: {
        type: String,
        default: null,
    },
    doctype: {
        type: String,
        default: null,
    },
    scan_name: {
        type: String,
        default: null
    }
})


const sessionStore = useSessionStore()


const controllerRef = ref(null)
const room = ref(null)
let realtimeHandler = null


const qrProperties = computed(() => {
    if (!room.value) return ''

    return JSON.stringify({
        room: room.value,
        device_type: 'web',
        server_url: window.location.origin,
        site_name: frappe.boot.site_name,
    })
})


function bindRealtime(roomName) {
    if (realtimeHandler && room.value) {
        frappe.realtime.off(room.value, realtimeHandler)
    }

    realtimeHandler = (data) => {
        if (data.device_type !== 'mobile') return

        if (data.event === 'scanner_added') {
            sessionStore.connecting()
        }
        else if (data.event === 'scanner_removed') {
            sessionStore.disconnect()
            createNewSession();
        }
        else if (data.event === 'signal') {
            controllerRef.value?.handleSignals(data.data)
        }
    }

    frappe.realtime.on(roomName, realtimeHandler)
}


function disconnectSession() {
    if (room.value && realtimeHandler) {
        frappe.realtime.off(room.value, realtimeHandler)
    }

    sessionStore.disconnect()
    room.value = null
    realtimeHandler = null
}

function createNewSession() {
    disconnectSession()

    const id = uuidv4()
    room.value = `doc_scanner_session_room+${id}`

    sessionStore.createSession(room.value)
    bindRealtime(room.value)
}

onMounted(() => {
    createNewSession()
})

onBeforeUnmount(() => {
    disconnectSession()
})
</script>

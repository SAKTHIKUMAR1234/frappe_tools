<template>
    <div class="container h-100 mt-2 d-flex flex-column ">
        <div class="d-flex justify-content-end p-10">
            <SessionIndicator />
        </div>
        <div class="mt-2 d-flex justify-content-center flex-column align-items-center"
            v-if="sessionStore.status == 'disconnected'">
            <QrCode :properties="qrProperties"></QrCode>
            <div>
                <small>
                    Scan this QR code with your mobile device to connect to the document scanner session.
                </small>
            </div>
        </div>
        <DocScannerController ref="controllerRef" />
    </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import SessionIndicator from '../Components/SessionIndicator.vue';
import { useSessionStore } from '../../Store/docscanner_store';
const sessionStore = useSessionStore();
import { v4 as uuidv4 } from 'uuid';
const uniqueId = uuidv4();
import QrCode from '../Components/QrCode.vue';
import DocScannerController from '../Components/DocScannerController.vue';

const room = `doc_scanner_session_room+${uniqueId}`

const controllerRef = ref(null);

const qrProperties = computed(() => {
    return JSON.stringify({
        "room": room,
        "device_type": "web",
        "server_url": window.location.origin,
        "site_name": frappe.boot.site_name
    })
})


frappe.realtime.on(room, (data) => {
    if (data.event === 'scanner_added' && data.device_type === 'mobile') {
        sessionStore.connect();
    }
    else if (data.event === 'scanner_removed' && data.device_type === 'mobile') {
        sessionStore.disconnect();
    }
    else if (data.event === 'signal' && data.device_type === 'mobile') {
        controllerRef.value.handleSignals(data.data);
    }
})


onMounted(() => {
    sessionStore.createSession(room);
});
</script>

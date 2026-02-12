<template>
    <div class="scanner-page">
        <div class="scanner-page-header">
            <SessionIndicator />
        </div>

        <DocScannerController ref="controllerRef" :is_new="props.is_new" :doctype="props.doctype"
            :scan_name="props.scan_name" :document_name="props.document_name"
            @reload_session="() => createNewSession()" />
    </div>
</template>

<script setup>
import { ref } from 'vue'
import SessionIndicator from '../Components/SessionIndicator.vue'
import DocScannerController from '../Components/DocScannerController.vue'

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

const controllerRef = ref(null)
</script>

<style scoped>
.scanner-page {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 8px 0;
    max-width: 100%;
}

.scanner-page-header {
    display: flex;
    justify-content: flex-end;
    padding: 0 4px;
}
</style>

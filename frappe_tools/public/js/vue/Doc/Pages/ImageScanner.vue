<template>
    <div class="container h-100 mt-2 d-flex flex-column">

        <div class="d-flex justify-content-end p-10">
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

<template>
    <div class="d-flex align-items-center gap-2">

        <div v-if="isLoading" class="spinner-border spinner-border-sm text-warning" role="status">
            <span class="visually-hidden">Loading...</span>
        </div>

        <span class="badge" :class="badgeClass">
            {{ label }}
        </span>

        <span class="text-muted small">
            {{ description }}
        </span>

    </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '../../Store/docscanner_store'

const sessionStore = useSessionStore()

const isLoading = computed(() =>
    sessionStore.status === 'loading'
)

const label = computed(() => {
    switch (sessionStore.status) {
        case 'connected':
            return 'Connected'
        case 'disconnected':
            return 'Disconnected'
    }
})

const badgeClass = computed(() => {
    switch (sessionStore.status) {
        case 'connected':
            return 'bg-success'
        case 'disconnected':
            return 'bg-danger'
    }
})

const description = computed(() => {
    switch (sessionStore.status) {
        case 'connected':
            return 'Scanner is connected'
        case 'disconnected':
            return 'Connection lost'
    }
})
</script>

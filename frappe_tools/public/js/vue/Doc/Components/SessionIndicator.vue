<template>
    <div class="session-indicator">

        <div v-if="isLoading" class="indicator-spinner">
            <div class="spinner-dot"></div>
        </div>

        <span class="indicator-badge" :class="statusClass">
            <span class="indicator-dot"></span>
            {{ label }}
        </span>

        <span class="indicator-description">
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

const statusClass = computed(() => {
    switch (sessionStore.status) {
        case 'connected':
            return 'status-connected'
        case 'disconnected':
            return 'status-disconnected'
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

<style scoped>
.session-indicator {
    display: flex;
    align-items: center;
    gap: 8px;
}

.indicator-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.2px;
}

.indicator-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    display: inline-block;
}

.status-connected {
    background: #ecfdf5;
    color: #059669;
    border: 1px solid #a7f3d0;
}

.status-connected .indicator-dot {
    background: #10b981;
    box-shadow: 0 0 6px rgba(16, 185, 129, 0.4);
}

.status-disconnected {
    background: #fef2f2;
    color: #dc2626;
    border: 1px solid #fca5a5;
}

.status-disconnected .indicator-dot {
    background: #ef4444;
    box-shadow: 0 0 6px rgba(239, 68, 68, 0.4);
}

.indicator-description {
    color: #94a3b8;
    font-size: 12px;
}

.indicator-spinner {
    width: 16px;
    height: 16px;
    display: flex;
    align-items: center;
    justify-content: center;
}

.spinner-dot {
    width: 14px;
    height: 14px;
    border: 2px solid #e2e8f0;
    border-top-color: #4f46e5;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
}

@keyframes spin {
    to { transform: rotate(360deg); }
}
</style>

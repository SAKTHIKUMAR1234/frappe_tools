<script setup>
import Button from "primevue/button";
defineProps({
  boot: { type: Object, default: null },
  run: { type: Object, default: null },
  view: { type: String, default: "queue" },
  queueStatus: { type: String, default: "all" },
});
defineEmits(["new", "open", "queue"]);
</script>

<template>
  <div class="capture-app">
    <header class="app-topbar">
      <nav class="topbar-nav" aria-label="Main navigation">
        <button
          :class="{ active: view === 'queue' }"
          @click="$emit('queue', 'all')"
        >
          <i class="pi pi-inbox" /> Documents
          <span v-if="boot?.queue?.counts?.ready" class="nav-count">{{
            boot.queue.counts.ready
          }}</span>
        </button>
      </nav>
      <div class="topbar-end">
        <span class="site-label" :title="boot?.site_name"
          ><i class="pi pi-server" />
          {{ boot?.site_name || "Local workspace" }}</span
        >
        <Button
          v-if="boot?.doctypes?.length"
          label="Add document"
          icon="pi pi-plus"
          size="small"
          @click="$emit('new')"
        />
        <span
          class="user-avatar"
          :title="boot?.user || 'Signed in'"
          :aria-label="boot?.user || 'Signed in'"
          >{{ (boot?.user || "U").slice(0, 1).toUpperCase() }}</span
        >
      </div>
    </header>
    <main
      class="capture-main"
      :class="{ 'is-review': view === 'run', 'is-intake': view === 'new' }"
    >
      <slot />
    </main>
  </div>
</template>

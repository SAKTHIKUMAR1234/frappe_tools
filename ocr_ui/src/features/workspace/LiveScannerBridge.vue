<script setup>
import { computed, onUnmounted, ref } from "vue";
import Button from "primevue/button";
import Tag from "primevue/tag";
import { call, errorMessage } from "@/services/api";
import { LiveImageAssembler } from "@/services/liveScanner";

const props = defineProps({
  layouts: { type: Array, default: () => [] },
  user: { type: String, default: "" },
  siteName: { type: String, default: "" },
});
const emit = defineEmits(["page"]);

const pin = ref("");
const state = ref("idle");
const error = ref("");
const received = ref(0);
const assembler = new LiveImageAssembler();
const stagedFolders = new Map();
const pendingCandidates = [];
const transferTimers = new Map();
let peer = null;
let channel = null;
let room = "";
let generation = 0;

const status = computed(() => {
  if (state.value === "idle")
    return { label: "Disconnected", severity: "secondary" };
  if (state.value === "failed")
    return { label: "Connection failed", severity: "danger" };
  if (state.value === "connected")
    return { label: "Phone connected", severity: "success" };
  if (state.value === "receiving")
    return { label: "Receiving scan", severity: "info" };
  if (state.value === "waiting")
    return { label: "Waiting for phone", severity: "warn" };
  return { label: "Starting", severity: "secondary" };
});

function newRoom() {
  return (
    globalThis.crypto?.randomUUID?.() ||
    `scanner-${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}

async function sendSignal(signal) {
  await call("frappe_tools.api.doc_scanner.send_signal", {
    room,
    device: "web",
    signal_data: signal,
  });
}

async function start() {
  const current = ++generation;
  cleanupPeer();
  error.value = "";
  state.value = "starting";
  room = newRoom();
  try {
    if (!globalThis.RTCPeerConnection) {
      throw new Error("This browser does not support WebRTC.");
    }
    pin.value = await call("frappe_tools.api.doc_scanner.register_pin", {
      room,
    });
    if (current !== generation) return;
    const iceServers = await call(
      "frappe_tools.api.doc_scanner.get_ice_servers",
    );
    if (current !== generation) return;
    peer = new RTCPeerConnection({
      iceServers: Array.isArray(iceServers) ? iceServers : [],
    });
    channel = peer.createDataChannel("scanner");
    channel.onopen = () => {
      if (current === generation) {
        state.value = "connected";
        sendBootstrap();
      }
    };
    channel.onclose = () => {
      if (current === generation) state.value = "waiting";
    };
    channel.onmessage = handleDataMessage;
    peer.onicecandidate = (event) => {
      if (event.candidate) {
        void sendSignal({
          type: "candidate",
          candidate: event.candidate.toJSON(),
        });
      }
    };
    peer.onconnectionstatechange = () => {
      if (current !== generation) return;
      if (peer?.connectionState === "connected") state.value = "connected";
      if (
        ["failed", "disconnected", "closed"].includes(peer?.connectionState)
      ) {
        state.value = "waiting";
      }
    };
    const offer = await peer.createOffer();
    if (current !== generation) return;
    await peer.setLocalDescription(offer);
    if (current !== generation) return;
    await sendSignal({ type: "offer", sdp: offer.sdp });
    if (current !== generation) return;
    state.value = "waiting";
    void pollSignals(current);
  } catch (caught) {
    if (current !== generation) return;
    cleanupPeer();
    error.value = errorMessage(caught, "Could not start the live scanner.");
    state.value = "failed";
  }
}

async function pollSignals(current) {
  while (current === generation && peer) {
    try {
      const signals = await call("frappe_tools.api.doc_scanner.get_signal", {
        room,
        device: "web",
        timeout: 2,
      });
      if (current !== generation) return;
      for (const signal of signals || []) await handleSignal(signal);
      await new Promise((resolve) => setTimeout(resolve, 650));
    } catch (caught) {
      if (current !== generation) return;
      error.value =
        caught?.message || "Live scanner signaling was interrupted.";
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
  }
}

async function handleSignal(signal) {
  if (!peer) return;
  if (signal?.type === "answer" && signal.sdp) {
    await peer.setRemoteDescription({ type: "answer", sdp: signal.sdp });
    for (const candidate of pendingCandidates.splice(0)) {
      await peer.addIceCandidate(candidate);
    }
  } else if (signal?.type === "candidate" && signal.candidate) {
    const candidate = new RTCIceCandidate(signal.candidate);
    if (peer.remoteDescription) await peer.addIceCandidate(candidate);
    else pendingCandidates.push(candidate);
  }
}

function handleDataMessage(event) {
  try {
    const payload = JSON.parse(event.data);
    if (payload?.type === "folder_begin") {
      stagedFolders.set(String(payload.folder_id), {
        ...payload,
        files: [],
      });
      state.value = "receiving";
      return;
    }
    if (payload?.type === "folder_complete") {
      const folder = stagedFolders.get(String(payload.folder_id));
      const count = Number(folder?.page_count || 0);
      if (
        folder &&
        count > 0 &&
        folder.files.length === count &&
        Array.from({ length: count }, (_, index) => folder.files[index]).every(
          Boolean,
        )
      ) {
        emit("page", { type: "folder", folder });
        state.value = "connected";
      }
      return;
    }
    if (payload?.type !== "chunk") return;
    state.value = "receiving";
    resetTransferTimer(payload.id);
    const result = assembler.add(payload);
    if (!result) return;
    clearTransferTimer(result.id);
    const file = new File([result.bytes], `phone-scan-${Date.now()}.jpg`, {
      type: result.mime,
    });
    Object.assign(file, { scannerMetadata: result.metadata });
    const folderId = String(result.metadata?.folder_id || "");
    const folder = folderId ? stagedFolders.get(folderId) : null;
    if (folder) {
      folder.files[Number(result.metadata?.page_index || 0)] = file;
    } else {
      emit("page", { type: "page", file });
    }
    received.value += 1;
    channel?.send(JSON.stringify({ type: "ack", id: result.id }));
    state.value = "connected";
  } catch (caught) {
    error.value = caught?.message || "The phone scan could not be received.";
    state.value = channel?.readyState === "open" ? "connected" : "waiting";
  }
}

function sendBootstrap() {
  if (channel?.readyState !== "open") return;
  channel.send(
    JSON.stringify({
      type: "bootstrap",
      protocol: 2,
      site: props.siteName || window.location.host,
      site_url: window.location.origin,
      site_label: props.siteName || window.location.host,
      user: props.user,
      layouts: props.layouts,
    }),
  );
}

function confirmFolder(folderId) {
  if (!folderId || channel?.readyState !== "open") return;
  channel.send(
    JSON.stringify({ type: "folder_committed", folder_id: folderId }),
  );
  stagedFolders.delete(String(folderId));
}

defineExpose({ confirmFolder });

function resetTransferTimer(id) {
  clearTransferTimer(id);
  transferTimers.set(
    id,
    setTimeout(() => {
      assembler.discard(id);
      transferTimers.delete(id);
      error.value =
        "The phone transfer timed out. Scan again; the browser kept existing pages.";
      state.value = channel?.readyState === "open" ? "connected" : "waiting";
    }, 120000),
  );
}

function clearTransferTimer(id) {
  const timer = transferTimers.get(id);
  if (timer) clearTimeout(timer);
  transferTimers.delete(id);
}

function openPhoneCamera() {
  if (channel?.readyState !== "open") return;
  channel.send(JSON.stringify({ type: "camera", message: "open_camera" }));
}

function cleanupPeer() {
  for (const timer of transferTimers.values()) clearTimeout(timer);
  transferTimers.clear();
  assembler.clear();
  stagedFolders.clear();
  pendingCandidates.splice(0);
  try {
    if (channel) channel.onopen = channel.onclose = channel.onmessage = null;
    if (peer) peer.onicecandidate = peer.onconnectionstatechange = null;
    channel?.close();
    peer?.close();
  } catch {
    // The next PIN must still be created even if browser WebRTC cleanup fails.
  }
  channel = null;
  peer = null;
}

function stop() {
  generation += 1;
  cleanupPeer();
  pin.value = "";
  error.value = "";
  state.value = "idle";
}
onUnmounted(stop);
</script>

<template>
  <section class="live-scanner-card" aria-label="Phone scanner connection">
    <div class="live-scanner-copy">
      <span class="live-scanner-icon"><i class="pi pi-mobile" /></span>
      <div>
        <strong>Phone scanner</strong>
        <small>{{
          state === "idle"
            ? "Connect only when you need to receive scans"
            : "Enter this PIN in the app—no API key or secret"
        }}</small>
      </div>
    </div>
    <div v-if="pin" class="live-scanner-pin" :aria-label="`Scanner PIN ${pin}`">
      {{ pin || "····" }}
    </div>
    <Tag
      v-if="state !== 'idle'"
      :value="status.label"
      :severity="status.severity"
    />
    <Button
      v-if="state === 'connected' || state === 'receiving'"
      label="Scan now"
      icon="pi pi-camera"
      size="small"
      :loading="state === 'receiving'"
      @click="openPhoneCamera"
    />
    <Button
      v-else
      :label="state === 'idle' ? 'Connect phone' : 'New PIN'"
      :icon="state === 'idle' ? 'pi pi-mobile' : 'pi pi-refresh'"
      size="small"
      outlined
      :loading="state === 'starting'"
      @click="start"
    />
    <Button
      v-if="state !== 'idle'"
      label="Disconnect"
      text
      size="small"
      @click="stop"
    />
    <span v-if="received" class="live-scanner-count"
      >{{ received }} received</span
    >
    <span v-if="error" class="live-scanner-error">{{ error }}</span>
  </section>
</template>

<style scoped>
.live-scanner-card {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 62px;
  margin: 12px 18px 0;
  padding: 10px 12px;
  border: 1px solid var(--surface-200);
  border-radius: 10px;
  background: var(--surface-50);
}

.live-scanner-copy {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 220px;
}

.live-scanner-copy div {
  display: grid;
  gap: 2px;
}

.live-scanner-copy small {
  color: var(--text-color-secondary);
}

.live-scanner-icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 8px;
  background: var(--surface-100);
}

.live-scanner-pin {
  min-width: 82px;
  font:
    700 20px/1 ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  letter-spacing: 0.16em;
  text-align: center;
}

.live-scanner-count {
  color: var(--text-color-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.live-scanner-error {
  color: var(--red-600);
  font-size: 12px;
}

@media (max-width: 900px) {
  .live-scanner-card {
    flex-wrap: wrap;
  }

  .live-scanner-copy {
    flex: 1 1 100%;
  }
}
</style>

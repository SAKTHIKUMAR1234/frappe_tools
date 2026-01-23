import { defineStore } from "pinia";
import { v4 as uuidv4 } from 'uuid';

export const useSessionStore = defineStore("session", {
  state: () => ({
    sessionId: null,
    status: "disconnected",
    mobile_status: "disconnected",
    pin: null,
    isSetupMode: false,
  }),

  actions: {
    initSession(force = false) {
      if (!this.sessionId || force) {
        this.sessionId = uuidv4();
        this.status = "disconnected";
        this.mobile_status = "disconnected";
        this.pin = null;
      }
      return this.sessionId;
    },

    resetSession() {
      return this.initSession(true);
    },

    createSession(sessionId) {
      this.sessionId = sessionId;
      frappe.call({
        method: "frappe_tools.api.doc_scanner.ping_to_device",
        args: {
          device_type: "web",
          event: "session_connected",
          room: sessionId,
        },
      });
    },

    async generatePin(force = false) {
      if (!this.sessionId || force) this.initSession(force);
      
      // If we are stuck in connecting or disconnected, allow resetting status when reload pin is clicked
      if (this.status !== 'connected' || force) {
        this.status = "disconnected";
        this.mobile_status = "disconnected";
      }

      try {
        const r = await frappe.call({
          method: "frappe_tools.api.doc_scanner.register_pin",
          args: { room: this.sessionId },
        });
        this.pin = r.message;
      } catch (e) {
        console.error("Failed to generate PIN", e);
      }
    },

    toggleSetupMode() {
      this.isSetupMode = !this.isSetupMode;
    },

    connecting() {
      this.status = "connecting";
      this.mobile_status = "connecting";
    },

    connect() {
      this.status = "connected";
      this.mobile_status = 'connected';
    },

    disconnect() {
      this.status = "disconnected";
      this.mobile_status = "disconnected";
      this.resetSession();
    },
  },
});

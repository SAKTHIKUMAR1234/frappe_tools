import { defineStore } from "pinia";

export const useSessionStore = defineStore("session", {
  state: () => ({
    sessionId: null,
    status: "disconnected",
    mobile_status: "disconnected",
  }),

  actions: {
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

    connect(room) {
      (this.status = "connected"), (this.mobile_status = "connected");
    },

    disconnect() {
      (this.status = "disconnected"), (this.mobile_status = "disconnected"), (this.sessionId = null);
    },
  },

});

export class LiveImageAssembler {
  constructor() {
    this.transfers = new Map();
  }

  add(payload) {
    if (payload?.type !== "chunk") return null;
    const id = String(payload.id || "");
    const index = Number(payload.index);
    const total = Number(payload.total);
    if (
      !id ||
      !Number.isInteger(index) ||
      !Number.isInteger(total) ||
      total < 1 ||
      total > 20000 ||
      index < 0 ||
      index >= total ||
      typeof payload.data !== "string"
    ) {
      throw new Error("The phone sent an invalid image chunk.");
    }

    let transfer = this.transfers.get(id);
    if (!transfer) {
      transfer = { total, received: 0, chunks: new Array(total) };
      this.transfers.set(id, transfer);
    }
    if (transfer.total !== total) {
      this.transfers.delete(id);
      throw new Error("The phone changed the image transfer size.");
    }
    if (transfer.chunks[index] === undefined) {
      transfer.chunks[index] = payload.data;
      transfer.received += 1;
    }
    if (transfer.received !== transfer.total) return null;

    this.transfers.delete(id);
    const envelope = JSON.parse(transfer.chunks.join(""));
    if (typeof envelope?.data !== "string" || !envelope.data) {
      throw new Error("The completed phone image is unreadable.");
    }
    const binary = atob(envelope.data);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return {
      id,
      bytes,
      mime: "image/jpeg",
      metadata:
        envelope.metadata && typeof envelope.metadata === "object"
          ? envelope.metadata
          : {},
    };
  }

  discard(id) {
    this.transfers.delete(id);
  }

  clear() {
    this.transfers.clear();
  }
}

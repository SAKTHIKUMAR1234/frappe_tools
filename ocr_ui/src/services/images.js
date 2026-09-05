export function makePage(file) {
  return {
    id: makePageId(),
    file,
    name: file.name,
    size: file.size,
    isPdf: isPdfFile(file),
    rotation: 0,
    preview: URL.createObjectURL(file),
  };
}

export function isPdfFile(file) {
  return (
    file?.type === "application/pdf" ||
    String(file?.name || "")
      .toLowerCase()
      .endsWith(".pdf")
  );
}

export function makePageId(webCrypto = globalThis.crypto) {
  if (typeof webCrypto?.randomUUID === "function") {
    return webCrypto.randomUUID();
  }
  if (typeof webCrypto?.getRandomValues === "function") {
    const bytes = webCrypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0"));
    return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
  }
  return `page-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function disposePage(page) {
  if (page?.preview) URL.revokeObjectURL(page.preview);
}

export async function materializePage(page) {
  if (!page.rotation) return page.file;
  const bitmap = await createImageBitmap(page.file);
  const quarterTurn = page.rotation % 180 !== 0;
  const canvas = document.createElement("canvas");
  canvas.width = quarterTurn ? bitmap.height : bitmap.width;
  canvas.height = quarterTurn ? bitmap.width : bitmap.height;
  const context = canvas.getContext("2d", { alpha: false });
  context.translate(canvas.width / 2, canvas.height / 2);
  context.rotate((page.rotation * Math.PI) / 180);
  context.drawImage(bitmap, -bitmap.width / 2, -bitmap.height / 2);
  bitmap.close();
  const blob = await new Promise((resolve, reject) => {
    canvas.toBlob(
      (value) =>
        value
          ? resolve(value)
          : reject(new Error("Could not rotate the page.")),
      page.file.type || "image/jpeg",
      0.96,
    );
  });
  return new File([blob], page.name, {
    type: blob.type,
    lastModified: Date.now(),
  });
}

export function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isDataUrl(value) {
  return typeof value === "string" && value.startsWith("data:image");
}

export async function imageToDataURL(src) {
  if (isDataUrl(src)) {
    return src;
  }

  const response = await fetch(src);
  const blob = await response.blob();
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

export function frappeCallAsync(method, args) {
  return new Promise((resolve, reject) => {
    frappe.call({
      method,
      args,
      callback: resolve,
      error: reject,
      freeze : true,
      freeze_msg : "Please Wait While Processing"
    });
  });
}

// Ignores a call while the previous one runs; `busy.value` is true until its last request ends.
export function oneAtATime(run, busy) {
  return async (...args) => {
    if (busy.value) return;
    busy.value = true;
    try {
      return await run(...args);
    } finally {
      busy.value = false;
    }
  };
}

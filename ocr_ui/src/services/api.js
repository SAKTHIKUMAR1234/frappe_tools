import axios from "axios";

const client = axios.create({ headers: { Accept: "application/json" } });

client.interceptors.request.use((config) => {
  const token = window.csrf_token || window.frappe?.csrf_token;
  if (token) config.headers["X-Frappe-CSRF-Token"] = token;
  return config;
});

export async function call(method, args = {}) {
  const { data } = await client.post(`/api/method/${method}`, args);
  return data.message;
}

export async function uploadPage(file, extraction) {
  const form = new FormData();
  form.append("file", file);
  form.append("is_private", "1");
  form.append("doctype", "Document Extraction");
  form.append("docname", extraction);
  form.append("fieldname", "pages");
  const { data } = await client.post("/api/method/upload_file", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data.message;
}

export function errorMessage(
  error,
  fallback = "Something went wrong. Please try again.",
) {
  const payload = error?.response?.data || {};
  if (payload._server_messages) {
    try {
      const messages = JSON.parse(payload._server_messages);
      const first = messages.length ? JSON.parse(messages[0]) : null;
      if (first?.message) return stripHtml(first.message);
    } catch {
      // Use the standard error fields below.
    }
  }
  if (payload.message) return stripHtml(payload.message);
  if (payload.exception)
    return stripHtml(String(payload.exception).split(":").at(-1));
  if (!error?.response)
    return "Cannot reach Frappe. Check the connection and retry.";
  return fallback;
}

function stripHtml(value) {
  const element = document.createElement("div");
  element.innerHTML = String(value || "");
  return (element.textContent || "").replace(/\s+/g, " ").trim();
}

export default client;

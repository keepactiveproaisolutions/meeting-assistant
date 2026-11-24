const getApiBaseUrl = () => {
  const rawEnv = (import.meta.env.VITE_API_URL || "").trim();
  if (!rawEnv) {
    // Prefer same-origin relative requests routed through the Vite proxy (/api and /ws)
    // so both localhost:5173 and LAN mobile clients (e.g. 192.168.1.8:5173) use port 5173.
    return "";
  }
  const host =
    typeof window !== "undefined" && window.location.hostname
      ? window.location.hostname
      : "localhost";
  if (
    host !== "localhost" &&
    host !== "127.0.0.1" &&
    (rawEnv.includes("localhost") || rawEnv.includes("127.0.0.1"))
  ) {
    return rawEnv
      .replace("localhost", host)
      .replace("127.0.0.1", host)
      .replace(/\/$/, "");
  }
  return rawEnv.replace(/\/$/, "");
};

const API_URL = getApiBaseUrl();
const TOKEN_KEY = "ma_token";

export const auth = {
  get token() {
    return localStorage.getItem(TOKEN_KEY);
  },
  set token(v) {
    v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY);
  },
  logout() {
    localStorage.removeItem(TOKEN_KEY);
  },
};

async function upload(path, formData) {
  const url = `${API_URL}/api/v1${path}`;
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: auth.token ? { Authorization: `Bearer ${auth.token}` } : {},
      body: formData,
    });
  } catch (networkErr) {
    throw new Error("Upload failed: the server could not be reached.");
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Upload failed (${res.status})`);
  }
  return res.json();
}

async function request(method, path, body) {
  const url = `${API_URL}/api/v1${path}`;
  let res;
  try {
    res = await fetch(url, {
      method,
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(auth.token ? { Authorization: `Bearer ${auth.token}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (networkErr) {
    console.error(`[API Network Error] ${method} ${url}:`, networkErr);
    throw new Error(
      "Unable to reach the Meeting Assistant server over the network. Please verify your phone is on the same Wi-Fi network and tap Retry."
    );
  }
  if (res.status === 401 && auth.token) auth.logout();
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = Array.isArray(err.detail)
      ? err.detail.map((d) => d.msg).join(", ")
      : err.detail;
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  baseUrl: API_URL,
  getConfig: () => request("GET", "/auth/config"),
  login: (email, password) => request("POST", "/auth/login", { email, password }),
  register: (payload) => request("POST", "/auth/register", payload),
  listMeetings: () => request("GET", "/meetings"),
  createMeeting: (title = "Live Meeting") => request("POST", "/meetings", { title }),
  joinMeeting: (code) => request("POST", "/meetings/join", { code }),
  getMeeting: (meetingId) => request("GET", `/meetings/${meetingId}`),
  ask: (meetingId, question, selectedText) =>
    request("POST", `/meetings/${meetingId}/ask`, {
      question,
      selected_text: selectedText || null,
    }),
  listDocuments: () => request("GET", "/documents"),
  getDocument: (id) => request("GET", `/documents/${id}`),
  uploadDocument: (file, docType) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("doc_type", docType);
    return upload("/documents", fd);
  },
  replaceDocument: (id, file) => {
    const fd = new FormData();
    fd.append("file", file);
    return upload(`/documents/${id}/replace`, fd);
  },
  setDocumentType: (id, docType) => request("PATCH", `/documents/${id}`, { doc_type: docType }),
  deleteDocument: (id) => request("DELETE", `/documents/${id}`),
  meetingDocuments: (meetingId) => request("GET", `/meetings/${meetingId}/documents`),
  setMeetingDocuments: (meetingId, ids) =>
    request("PUT", `/meetings/${meetingId}/documents`, { document_ids: ids }),
  getDemoPrep: (meetingId) => request("GET", `/demo-prep?meeting_id=${meetingId}`),
  generateDemoPrep: (meetingId) => request("POST", `/demo-prep/generate?meeting_id=${meetingId}`),
};

export function meetingSocketUrl(meetingId) {
  const tokenParam = auth.token ? `?token=${encodeURIComponent(auth.token)}` : "";
  if (API_URL) {
    const base = API_URL.replace(/^http/, "ws");
    return `${base}/ws/meetings/${meetingId}${tokenParam}`;
  }
  const proto =
    typeof window !== "undefined" && window.location.protocol === "https:"
      ? "wss:"
      : "ws:";
  const host =
    typeof window !== "undefined" && window.location.host
      ? window.location.host
      : "localhost:5173";
  return `${proto}//${host}/ws/meetings/${meetingId}${tokenParam}`;
}

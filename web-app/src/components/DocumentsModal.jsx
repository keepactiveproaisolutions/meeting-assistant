import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../services/api";

const DOC_TYPES = [
  ["cv", "CV / Resume"],
  ["jd", "Job description"],
  ["project", "Client project notes"],
  ["work", "My project / work sample"],
  ["supporting", "Supporting document"],
];
const STATUS = {
  ready: ["Ready", "st-ready"],
  failed: ["Failed", "st-failed"],
  processing: ["Indexing…", "st-busy"],
  pending: ["Queued…", "st-busy"],
};

export default function DocumentsModal({ meetingId, onClose }) {
  const [docs, setDocs] = useState([]);
  const [checked, setChecked] = useState({});
  const [docType, setDocType] = useState("cv");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(null);
  const fileRef = useRef(null);
  const replaceRef = useRef(null);
  const replaceTarget = useRef(null);

  const load = useCallback(async () => {
    try {
      const data = await api.meetingDocuments(meetingId);
      setDocs(data.documents);
      setChecked(Object.fromEntries(data.documents.map((d) => [d.id, !!d.selected])));
    } catch (e) {
      setErr(e.message);
    }
  }, [meetingId]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while anything is still being extracted / indexed
  useEffect(() => {
    if (!docs.some((d) => d.status === "pending" || d.status === "processing")) return undefined;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [docs, load]);

  const run = async (fn, okMsg) => {
    setErr("");
    setMsg("");
    setBusy(true);
    try {
      const res = await fn();
      if (okMsg) setMsg(typeof okMsg === "function" ? okMsg(res) : okMsg);
      await load();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const onUpload = (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    run(
      () => api.uploadDocument(file, docType),
      (r) =>
        r.duplicate
          ? `“${r.filename}” is already uploaded (identical file) — type updated.`
          : `Uploaded “${r.filename}” — extracting and indexing…`
    );
  };

  const onReplacePicked = (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    const d = replaceTarget.current;
    if (!file || !d) return;
    if (!window.confirm(`Replace “${d.filename}” with “${file.name}”? Its meeting selections are kept.`)) return;
    run(() => api.replaceDocument(d.id, file), "Replacement uploaded — re-indexing…");
  };

  const remove = (d) => {
    if (!window.confirm(`Permanently remove “${d.filename}” and its indexed passages?`)) return;
    run(() => api.deleteDocument(d.id), `Removed “${d.filename}”.`);
  };

  const saveSelection = () => {
    const ready = docs.filter((d) => d.status === "ready").map((d) => d.id);
    const chosen = ready.filter((id) => checked[id]);
    const all = chosen.length === ready.length;
    run(
      () => api.setMeetingDocuments(meetingId, all ? null : chosen),
      all ? "This meeting uses all of your ready documents." : `This meeting uses ${chosen.length} selected document(s).`
    );
  };

  const openPreview = async (d) => {
    setErr("");
    try {
      setPreview(await api.getDocument(d.id));
    } catch (e) {
      setErr(e.message);
    }
  };

  return (
    <div className="diagram-modal-backdrop" onClick={onClose}>
      <div className="docs-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="CV and documents">
        <div className="diagram-modal-header">
          <strong>👤 CV &amp; Documents</strong>
          <button type="button" className="small btn-close-modal" onClick={onClose}>
            ✕ Close
          </button>
        </div>

        <div className="docs-body">
          <p className="docs-hint">
            Answers and Demo Prep use only the documents ticked for this meeting. Supported: text-based PDF, Word
            .docx, .txt/.md. Scanned/image-only PDFs are rejected with a clear message (no OCR available).
          </p>

          <div className="docs-upload">
            <select value={docType} onChange={(e) => setDocType(e.target.value)} aria-label="Document type">
              {DOC_TYPES.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
            <button type="button" disabled={busy} onClick={() => fileRef.current?.click()}>
              ⬆ Upload file…
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx,.txt,.md,application/pdf,text/plain"
              onChange={onUpload}
              hidden
            />
            <input ref={replaceRef} type="file" accept=".pdf,.docx,.txt,.md" onChange={onReplacePicked} hidden />
          </div>

          {err && (
            <div className="error-box" role="alert">
              {err}
            </div>
          )}
          {msg && <div className="docs-msg">{msg}</div>}

          {docs.length === 0 && <p className="muted">No documents yet — upload your CV and the job description.</p>}

          <ul className="docs-list">
            {docs.map((d) => {
              const [label, cls] = STATUS[d.status] || [d.status, ""];
              return (
                <li key={d.id} className="doc-card">
                  <label className="doc-use">
                    <input
                      type="checkbox"
                      disabled={d.status !== "ready"}
                      checked={!!checked[d.id]}
                      onChange={(e) => setChecked((c) => ({ ...c, [d.id]: e.target.checked }))}
                    />
                    <span>Use</span>
                  </label>
                  <div className="doc-main">
                    <div className="doc-title">
                      <span className="doc-name">{d.filename}</span>
                      <span className={`doc-status ${cls}`}>{label}</span>
                    </div>
                    <div className="doc-meta">
                      <select
                        value={d.doc_type}
                        onChange={(e) => run(() => api.setDocumentType(d.id, e.target.value), "Type updated.")}
                        aria-label="Change document type"
                      >
                        {DOC_TYPES.map(([v, l]) => (
                          <option key={v} value={v}>
                            {l}
                          </option>
                        ))}
                      </select>
                      {d.status === "ready" && (
                        <span>
                          {d.chunk_count} passages{d.page_count ? ` · ${d.page_count} page(s)` : ""}
                          {d.char_count ? ` · ${d.char_count.toLocaleString()} chars` : ""}
                        </span>
                      )}
                    </div>
                    {d.status === "failed" && <div className="doc-error">{d.error}</div>}
                    {d.warnings?.length > 0 && <div className="doc-warn">⚠ {d.warnings.join("; ")}</div>}
                    <div className="doc-actions">
                      <button type="button" className="small" onClick={() => openPreview(d)}>
                        👁 Preview
                      </button>
                      <button
                        type="button"
                        className="small"
                        onClick={() => {
                          replaceTarget.current = d;
                          replaceRef.current?.click();
                        }}
                      >
                        ♻ Replace
                      </button>
                      <button type="button" className="small btn-danger" onClick={() => remove(d)}>
                        🗑 Remove
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="docs-footer">
          <button type="button" disabled={busy || !docs.some((d) => d.status === "ready")} onClick={saveSelection}>
            ✔ Save meeting selection
          </button>
        </div>

        {preview && (
          <div className="doc-preview" onClick={(e) => e.stopPropagation()}>
            <div className="diagram-modal-header">
              <strong>Extracted text — {preview.filename}</strong>
              <button type="button" className="small" onClick={() => setPreview(null)}>
                ✕
              </button>
            </div>
            <div className="doc-preview-meta">
              {preview.doc_type_label} · {preview.chunk_count} passages
              {preview.extraction_method ? ` · ${preview.extraction_method}` : ""}
              {preview.sections?.length ? ` · Sections: ${preview.sections.slice(0, 12).join(", ")}` : ""}
              <br />
              Contact details are redacted before indexing.
              {preview.text_truncated ? " Showing the first 20,000 characters." : ""}
            </div>
            <pre className="doc-preview-text">{preview.text || preview.error || "(no text extracted)"}</pre>
          </div>
        )}
      </div>
    </div>
  );
}

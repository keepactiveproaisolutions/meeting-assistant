import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { copyText } from "../utils/clipboard";

export const fmtTs = (ms) => {
  const s = Math.floor(ms / 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(Math.floor(s / 3600))}:${p(Math.floor((s % 3600) / 60))}:${p(s % 60)}`;
};

export default function TranscriptPanel({
  utterances = [],
  segments = [],
  partials = {},
  questionBySegment = {},
  onSelectionChange = () => {},
  onSendDirect = () => {},
  expandedPane = null,
  onToggleExpand = null,
}) {
  const [viewMode, setViewMode] = useState("paragraphs"); // "paragraphs" | "raw"
  const listRef = useRef(null);
  const [sendingId, setSendingId] = useState(null);

  const handleSendDirect = (qText, uId) => {
    if (sendingId) return; // prevent duplicate clicks while submitting
    setSendingId(uId);
    onSendDirect(qText);
    setTimeout(() => setSendingId(null), 1800);
  };
  const endRef = useRef(null);
  const isNearBottomRef = useRef(true);

  const handleScroll = () => {
    if (!listRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = listRef.current;
    isNearBottomRef.current = scrollHeight - scrollTop - clientHeight < 60;
  };

  useEffect(() => {
    if (isNearBottomRef.current) {
      endRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [utterances, segments, viewMode, partials]);

  // Provisional (not yet final) Live text, in spoken order. Never copied or sent by itself.
  const partialList = Object.values(partials || {}).sort((a, b) => a.start_offset_ms - b.start_offset_ms);
  const liveText = partialList.map((p) => p.text).join(" ").trim();
  // Inline placement: provisional words continue the latest paragraph (same pause rule as the server's
  // paragraph split), otherwise they open a pending card in the same layout right after it.
  const JOIN_GAP_MS = 1500;
  const baseUtterances = utterances || [];
  const lastUtt = baseUtterances[baseUtterances.length - 1];
  let cards = baseUtterances;
  let liveTargetId = null;
  if (liveText) {
    const liveStart = partialList[0].start_offset_ms;
    if (lastUtt && liveStart >= lastUtt.start_offset_ms
        && liveStart - (lastUtt.end_offset_ms ?? lastUtt.start_offset_ms) < JOIN_GAP_MS
        && !(lastUtt.text || "").trim().endsWith("?")) { // server starts a new card after a question
      liveTargetId = lastUtt.id;
    } else {
      liveTargetId = "__live__";
      cards = [...baseUtterances, { id: "__live__", text: "", start_offset_ms: liveStart,
        end_offset_ms: liveStart, is_final: false, questions: [] }];
    }
  }

  const copy = (text) => {
    copyText(text);
  };

  const copyCleanText = () => {
    const list = utterances.length ? utterances : segments;
    copy(list.map((item) => item.text).join("\n\n"));
  };

  const copyAll = () => {
    if (viewMode === "raw" || (!utterances.length && segments.length)) {
      copy(segments.map((s) => `[${fmtTs(s.start_offset_ms)}] ${s.text}`).join("\n"));
    } else {
      copy(utterances.map((u) => `[${fmtTs(u.start_offset_ms)}] ${u.text}`).join("\n\n"));
    }
  };

  const handleSelect = () => {
    const sel = window.getSelection();
    const text = sel?.toString().trim() || "";
    const inside = sel?.anchorNode && listRef.current?.contains(sel.anchorNode);
    onSelectionChange(inside ? text : "");
  };

  const safeUtterances = utterances || [];
  const safeSegments = segments || [];
  const isExpanded = expandedPane === "transcript";

  return (
    <section className="panel">
      <header className="panel-header">
        <div className="panel-header-left">
          <h2>Live transcript</h2>
          <div className="view-toggle">
            <button
              type="button"
              className={`toggle-tab ${viewMode === "paragraphs" ? "active" : ""}`}
              onClick={() => setViewMode("paragraphs")}
            >
              Paragraphs
            </button>
            <button
              type="button"
              className={`toggle-tab ${viewMode === "raw" ? "active" : ""}`}
              onClick={() => setViewMode("raw")}
            >
              Raw
            </button>
          </div>
        </div>
        <div className="panel-header-actions">
          <button
            type="button"
            className="small"
            onClick={copyCleanText}
            disabled={!safeUtterances.length && !safeSegments.length}
            title="Copy pure text without timestamps"
          >
            Copy Text
          </button>
          <button
            type="button"
            className="small btn-primary-small"
            onClick={copyAll}
            disabled={!safeUtterances.length && !safeSegments.length}
            title="Copy full transcript with timestamps"
          >
            Copy All
          </button>
          {onToggleExpand && (
            <button
              type="button"
              className="small desktop-only-btn"
              onClick={onToggleExpand}
              title={isExpanded ? "Restore 45/55 split" : "Expand Live Transcript panel"}
            >
              {isExpanded ? "❐ Restore Split" : "◧ Expand"}
            </button>
          )}
        </div>
      </header>

      <div
        className="panel-body transcript-body"
        ref={listRef}
        onScroll={handleScroll}
        onMouseUp={handleSelect}
        onKeyUp={handleSelect}
      >
        {viewMode === "paragraphs" ? (
          cards.length === 0 ? (
            <p className="muted">Waiting for speech…</p>
          ) : (
            cards.map((u) => {
              const hasQ = u.is_question || (u.questions && u.questions.length > 0);
              const tail = u.id === liveTargetId ? liveText : "";
              const hasFinal = Boolean((u.text || "").trim());
              const sendText = `${(u.text || "").trim()} ${tail || ""}`.trim();
              return (
                <motion.div
                  // Keyed by start time: the pending live card and the finalized paragraph that replaces it are
                  // the SAME element (updated in place, no remount / flicker).
                  key={`card-${u.start_offset_ms}`}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.14 }}
                  className={`paragraph-card ${hasQ ? "question-card" : ""}`}
                >
                  <div className="paragraph-header">
                    <span className="ts">[{fmtTs(u.start_offset_ms)}]</span>
                    {(!u.is_final || tail) && <span className="live-tag">● live</span>}
                    <div className="paragraph-actions">
                      <button
                        type="button"
                        className={`small ${hasQ ? "btn-ask-ai" : "btn-send"}`}
                        onClick={() => sendText && handleSendDirect(sendText, u.id)}
                        disabled={sendingId === u.id || !sendText}
                        title={tail ? "Send what has been heard so far to AI" : "Send this paragraph to AI"}
                      >
                        {sendingId === u.id ? "Sent…" : "Send"}
                      </button>
                      <button
                        type="button"
                        className="small"
                        onClick={() => copy(`[${fmtTs(u.start_offset_ms)}] ${sendText}`)}
                        disabled={!sendText}
                        title="Copy this paragraph"
                      >
                        Copy
                      </button>
                    </div>
                  </div>
                  <div className="paragraph-text">
                    {u.text}
                    {tail && (
                      <span className="live-tail" aria-live="polite">
                        {hasFinal ? " " : ""}
                        {tail}
                      </span>
                    )}
                  </div>
                </motion.div>
              );
            })
          )
        ) : safeSegments.length === 0 ? (
          <p className="muted">Waiting for audio chunks…</p>
        ) : (
          safeSegments.map((s) => {
            const q = questionBySegment[s.id];
            return (
              <div key={s.id} className={`line ${q ? "question" : ""}`}>
                <span className="ts">[{fmtTs(s.start_offset_ms)}]</span>
                <span className="text">{s.text}</span>
                <button
                  type="button"
                  className={`small ${q ? "btn-ask-ai" : "btn-send"}`}
                  onClick={() => onUse(q ? q.text : s.text)}
                  title="Put this text in the question box to review, then press Send"
                >
                  Use
                </button>
                <button type="button" className="small" onClick={() => onAppend(s.text)} title="Append to the question box">
                  + Add
                </button>
                <button
                  type="button"
                  className="small"
                  onClick={() => copy(`[${fmtTs(s.start_offset_ms)}] ${s.text}`)}
                >
                  Copy
                </button>
              </div>
            );
          })
        )}
        <div ref={endRef} />
      </div>
    </section>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import AnswerPanel from "../components/AnswerPanel";
import AskBox from "../components/AskBox";
import DemoPrepModal from "../components/DemoPrepModal";
import DocumentsModal from "../components/DocumentsModal";
import TranscriptPanel from "../components/TranscriptPanel";
import { useMeetingSocket } from "../hooks/useMeetingSocket";
import { api } from "../services/api";
import { copyText } from "../utils/clipboard";

export default function Meeting() {
  const { meetingId } = useParams();
  const [text, setText] = useState("");
  const [selection, setSelection] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [mobileTab, setMobileTab] = useState("answers"); // "transcript" | "answers"
  const [showDocs, setShowDocs] = useState(false);
  const [showPrep, setShowPrep] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  // Resizable desktop workspace splitter (45% Transcript / 55% AI Answer default)
  const [splitPct, setSplitPct] = useState(() => {
    const saved = parseFloat(localStorage.getItem("ma_split_ratio") || "45");
    return Number.isFinite(saved) ? Math.min(78, Math.max(22, saved)) : 45;
  });
  const [expandedPane, setExpandedPane] = useState(null); // null | "transcript" | "answers"
  const columnsRef = useRef(null);
  const draggingRef = useRef(false);

  const {
    meeting,
    segments,
    utterances,
    partials,
    questionBySegment,
    answers,
    connection,
    presence,
    ended,
    socketError,
    retryConnection,
  } = useMeetingSocket(meetingId);

  // Drag splitter logic for desktop
  const onDividerMouseDown = useCallback((e) => {
    e.preventDefault();
    draggingRef.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  useEffect(() => {
    const onMove = (e) => {
      if (!draggingRef.current || !columnsRef.current) return;
      const rect = columnsRef.current.getBoundingClientRect();
      if (rect.width <= 200) return;
      const pct = ((e.clientX - rect.left) / rect.width) * 100;
      const clamped = Math.min(78, Math.max(22, Math.round(pct * 10) / 10));
      setExpandedPane(null);
      setSplitPct(clamped);
      localStorage.setItem("ma_split_ratio", String(clamped));
    };
    const onUp = () => {
      if (draggingRef.current) {
        draggingRef.current = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      }
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const resetSplit = () => {
    setExpandedPane(null);
    setSplitPct(45);
    localStorage.setItem("ma_split_ratio", "45");
  };

  const toggleExpandTranscript = () => {
    setExpandedPane((prev) => (prev === "transcript" ? null : "transcript"));
  };

  const toggleExpandAnswers = () => {
    setExpandedPane((prev) => (prev === "answers" ? null : "answers"));
  };

  const effectiveLeftPct =
    expandedPane === "transcript" ? 76 : expandedPane === "answers" ? 24 : splitPct;

  const send = async (question, selectedText) => {
    setError("");
    try {
      await api.ask(meetingId, question, selectedText);
      setText("");
      setSelection("");
      setMobileTab("answers");
      window.getSelection()?.removeAllRanges();
    } catch (err) {
      setError(err.message);
    }
  };

  const copyCode = () => {
    if (meeting?.code) {
      copyText(meeting.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="meeting-page">
      <header className="topbar">
        <Link to="/" className="nav-back">
          ← Setup
        </Link>
        <strong className="meeting-title">{meeting?.title || "Meeting"}</strong>
        {meeting?.code && (
          <div className="code-badge-group">
            <span className="badge code">Code: {meeting.code}</span>
            <button type="button" className="small" onClick={copyCode}>
              {copied ? "✓ Copied" : "Copy"}
            </button>
          </div>
        )}
        <span className={`badge ${connection}`}>{connection}</span>
        <span className="muted presence-label">{presence} client(s)</span>
        {ended && <span className="badge ended">Ended</span>}
        <div className="topbar-right">
          <button
            type="button"
            className="small desktop-only-btn"
            onClick={resetSplit}
            title="Reset workspace split to 45% Transcript / 55% AI Answer"
          >
            ⚖️ 45:55
          </button>
          <button type="button" className="small btn-demo-prep header-action" onClick={() => setShowPrep(true)}>
            🎯 Demo Prep
          </button>
          <button type="button" className="small btn-docs header-action" onClick={() => setShowDocs(true)}>
            👤 Upload CV / Documents
          </button>
          <div className="header-menu">
            <button
              type="button"
              className="small header-menu-btn"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((o) => !o)}
            >
              ⋯
            </button>
            {menuOpen && (
              <div className="header-menu-list" role="menu" onClick={() => setMenuOpen(false)}>
                <button type="button" role="menuitem" onClick={() => setShowPrep(true)}>
                  🎯 Demo Prep
                </button>
                <button type="button" role="menuitem" onClick={() => setShowDocs(true)}>
                  👤 Upload CV / Documents
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Mobile Tab Switcher (visible on narrow screens <= 820px; keeps panels mounted!) */}
      <nav className="mobile-tabs" aria-label="Meeting view tabs">
        <button
          type="button"
          className={`mobile-tab-btn ${mobileTab === "transcript" ? "active" : ""}`}
          onClick={() => setMobileTab("transcript")}
        >
          🎙️ Transcript ({utterances?.length || segments?.length || 0})
        </button>
        <button
          type="button"
          className={`mobile-tab-btn ${mobileTab === "answers" ? "active" : ""}`}
          onClick={() => setMobileTab("answers")}
        >
          💡 Answers ({answers?.length || 0})
        </button>
      </nav>

      <div
        className="columns"
        ref={columnsRef}
        style={{
          "--left-col": `${effectiveLeftPct}fr`,
          "--right-col": `${Math.max(10, 100 - effectiveLeftPct)}fr`,
        }}
      >
        <div
          className={`column-slot ${
            mobileTab === "transcript" ? "mobile-slot-active" : "mobile-slot-hidden"
          }`}
        >
          <TranscriptPanel
            utterances={utterances}
            segments={segments}
            partials={partials}
            questionBySegment={questionBySegment}
            onSelectionChange={setSelection}
            onSendDirect={(q) => send(q, "")}
            expandedPane={expandedPane}
            onToggleExpand={toggleExpandTranscript}
          />
        </div>

        <div
          className="splitter-divider"
          onMouseDown={onDividerMouseDown}
          onDoubleClick={resetSplit}
          title="Drag to resize Transcript / Answer panels (double-click to reset 45:55)"
          role="separator"
          aria-orientation="vertical"
        />

        <div
          className={`column-slot ${
            mobileTab === "answers" ? "mobile-slot-active" : "mobile-slot-hidden"
          }`}
        >
          <AnswerPanel
            answers={answers}
            expandedPane={expandedPane}
            onToggleExpand={toggleExpandAnswers}
          />
        </div>
      </div>

      {(error || socketError) && (
        <div className="error-box" style={{ margin: "4px 10px 0" }} role="alert">
          <div style={{ flex: 1 }}>
            <strong>Connection notice:</strong> {error || socketError}
          </div>
          <button
            type="button"
            className="small btn-retry"
            onClick={() => {
              setError("");
              retryConnection();
            }}
          >
            🔄 Retry
          </button>
        </div>
      )}

      <AskBox
        value={text}
        onChange={setText}
        selection={selection}
        onClearSelection={() => setSelection("")}
        onSend={send}
        disabled={connection !== "connected"}
      />

      {showPrep && <DemoPrepModal meetingId={meetingId} onClose={() => setShowPrep(false)} />}
      {showDocs && <DocumentsModal meetingId={meetingId} onClose={() => setShowDocs(false)} />}
    </div>
  );
}

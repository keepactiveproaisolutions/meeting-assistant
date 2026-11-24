import { useEffect, useRef } from "react";

export default function AskBox({ value, onChange, selection, onClearSelection, onSend, disabled }) {
  const taRef = useRef(null);
  const lastValue = useRef(value);

  // When a transcript card fills the box, grow it to show the whole question and focus it for review/editing.
  useEffect(() => {
    const el = taRef.current;
    if (el && value !== lastValue.current && document.activeElement !== el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(160, el.scrollHeight)}px`;
      if (value) el.focus({ preventScroll: true });
    }
    lastValue.current = value;
  }, [value]);

  const submit = (e) => {
    e?.preventDefault();
    const typed = value.trim();
    const question = typed || selection;
    if (!question || disabled) return;
    onSend(question, typed ? selection : "");
    if (taRef.current) {
      taRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit(e);
    }
  };

  const handleInput = (e) => {
    onChange(e.target.value);
    const el = taRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(120, el.scrollHeight)}px`;
    }
  };

  return (
    <form className="askbox-wrap" onSubmit={submit}>
      {selection && (
        <div className="selection-chip">
          <span className="selection-chip-text">
            Selected context: “{selection.length > 90 ? `${selection.slice(0, 90)}…` : selection}”
          </span>
          <button
            type="button"
            className="small selection-chip-clear"
            title="Move the selected text into the box so you can edit it before sending"
            onClick={() => {
              onChange(value.trim() ? `${value.trim()}\n${selection}` : selection);
              onClearSelection?.();
              setTimeout(() => {
                const el = taRef.current;
                if (el) {
                  el.focus();
                  el.style.height = "auto";
                  el.style.height = `${Math.min(120, el.scrollHeight)}px`;
                }
              }, 0);
            }}
          >
            ✎ Edit
          </button>
          {onClearSelection && (
            <button
              type="button"
              className="small selection-chip-clear"
              onClick={onClearSelection}
            >
              ✕
            </button>
          )}
        </div>
      )}
      <div className="askbox">
        <textarea
          ref={taRef}
          rows={1}
          value={value}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          placeholder={
            selection
              ? "Type a question about the selected transcript, or press Send…"
              : "Ask a question, request an architecture diagram, or ask for database DDL…"
          }
        />
        <button
          type="submit"
          className="btn-ask-submit"
          disabled={disabled || (!value.trim() && !selection)}
        >
          Send
        </button>
      </div>
    </form>
  );
}

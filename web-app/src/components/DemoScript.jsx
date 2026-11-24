import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { CodeBlockView, DiagramView, DiagramZoomModal } from "./AnswerPanel";
import { copyText } from "../utils/clipboard";

const Icon = {
  left: (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M15 18l-6-6 6-6" />
    </svg>
  ),
  right: (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 18l6-6-6-6" />
    </svg>
  ),
  copy: (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </svg>
  ),
  check: (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6L9 17l-5-5" />
    </svg>
  ),
};

const FONT_KEY = "ma_script_font";

export default function DemoScript({ steps }) {
  const [i, setI] = useState(0);
  const [dir, setDir] = useState(1);
  const [copied, setCopied] = useState(false);
  const [zoom, setZoom] = useState(null);
  const [font, setFont] = useState(() => {
    try {
      return Math.min(28, Math.max(15, parseInt(localStorage.getItem(FONT_KEY) || "20", 10)));
    } catch {
      return 20;
    }
  });
  const reduce = useReducedMotion();
  const total = steps.length;
  const step = steps[Math.min(i, total - 1)];

  const sections = useMemo(() => {
    const seen = new Map();
    steps.forEach((s, idx) => {
      if (!seen.has(s.section)) seen.set(s.section, idx);
    });
    return [...seen.entries()];
  }, [steps]);

  const go = useCallback(
    (to) => {
      const t = Math.max(0, Math.min(total - 1, to));
      setDir(t >= i ? 1 : -1);
      setI(t);
      setCopied(false);
    },
    [i, total]
  );

  useEffect(() => {
    const onKey = (e) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target?.tagName)) return;
      if (e.key === "ArrowRight" || e.key === "PageDown") go(i + 1);
      if (e.key === "ArrowLeft" || e.key === "PageUp") go(i - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, i]);

  const setFontSize = (v) => {
    const f = Math.min(28, Math.max(15, v));
    setFont(f);
    try {
      localStorage.setItem(FONT_KEY, String(f));
    } catch {
      /* storage unavailable */
    }
  };

  const copy = () => {
    const text = [step.say, ...(step.points || []).map((p) => `• ${p}`)].filter(Boolean).join("\n");
    copyText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  if (!step) return null;
  const variants = reduce
    ? { enter: { opacity: 0 }, center: { opacity: 1 }, exit: { opacity: 0 } }
    : {
        enter: (d) => ({ opacity: 0, x: 18 * d }),
        center: { opacity: 1, x: 0 },
        exit: (d) => ({ opacity: 0, x: -12 * d }),
      };

  return (
    <div className="script" style={{ "--script-font": `${font}px` }}>
      <div className="script-top">
        <div className="script-progress" role="progressbar" aria-valuemin={1} aria-valuemax={total} aria-valuenow={i + 1}>
          <motion.div
            className="script-progress-bar"
            animate={{ width: `${((i + 1) / total) * 100}%` }}
            transition={{ duration: reduce ? 0 : 0.25, ease: "easeOut" }}
          />
        </div>
        <div className="script-nav-row">
          <label className="script-jump">
            <span className="sr-only">Jump to step</span>
            <select value={i} onChange={(e) => go(Number(e.target.value))} aria-label="Jump to step">
              {steps.map((s, idx) => (
                <option key={idx} value={idx}>
                  {idx + 1}. {s.section} — {String(s.title || "").slice(0, 70)}
                </option>
              ))}
            </select>
          </label>
          <span className="script-count">
            {i + 1} / {total}
          </span>
          <div className="script-font" role="group" aria-label="Text size">
            <button type="button" onClick={() => setFontSize(font - 1)} aria-label="Smaller text">
              A−
            </button>
            <button type="button" onClick={() => setFontSize(font + 1)} aria-label="Larger text">
              A+
            </button>
          </div>
        </div>
        <div className="script-sections" role="tablist" aria-label="Sections">
          {sections.map(([name, idx]) => (
            <button
              key={name}
              type="button"
              role="tab"
              aria-selected={step.section === name}
              className={step.section === name ? "active" : ""}
              onClick={() => go(idx)}
            >
              {name}
            </button>
          ))}
        </div>
      </div>

      <div className="script-stage" aria-live="polite">
        <AnimatePresence mode="wait" custom={dir} initial={false}>
          <motion.article
            key={i}
            className="script-card"
            custom={dir}
            variants={variants}
            initial="enter"
            animate="center"
            exit="exit"
            transition={{ duration: reduce ? 0.12 : 0.2, ease: "easeOut" }}
          >
            <div className="script-kicker">
              Step {i + 1} · {step.section}
            </div>
            <h2 className="script-title">{step.title}</h2>
            {step.say && (
              <div className="script-say">
                {step.say.split(/\n{2,}/).map((para, k) => (
                  <p key={k}>{para}</p>
                ))}
              </div>
            )}
            {step.points?.length > 0 && (
              <ul className="script-points">
                {step.points.map((p, k) => (
                  <li key={k}>{p}</li>
                ))}
              </ul>
            )}
            {step.diagram && <DiagramView diagram={step.diagram} onOpenZoom={setZoom} />}
            {step.code?.code && <CodeBlockView lang={step.code.lang} code={step.code.code} />}
            {step.explain?.length > 0 && (
              <div className="script-explain">
                <div className="script-explain-title">How to explain it</div>
                <ol>
                  {step.explain.map((x, k) => (
                    <li key={k}>{x}</li>
                  ))}
                </ol>
              </div>
            )}
            {step.note && <p className="script-note">Tip: {step.note}</p>}
          </motion.article>
        </AnimatePresence>
      </div>

      <div className="script-bottom">
        <button type="button" className="script-btn" onClick={() => go(i - 1)} disabled={i === 0}>
          {Icon.left}
          <span>Back</span>
        </button>
        <button type="button" className="script-btn script-btn-copy" onClick={copy}>
          {copied ? Icon.check : Icon.copy}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
        <button type="button" className="script-btn script-btn-next" onClick={() => go(i + 1)} disabled={i === total - 1}>
          <span>Next</span>
          {Icon.right}
        </button>
      </div>

      {zoom && (
        <div onClick={(e) => e.stopPropagation()}>
          <DiagramZoomModal diagram={zoom} onClose={() => setZoom(null)} />
        </div>
      )}
    </div>
  );
}

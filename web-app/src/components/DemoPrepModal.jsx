import { useEffect, useState } from "react";
import { api } from "../services/api";
import { CodeBlockView, DiagramView, DiagramZoomModal } from "./AnswerPanel";
import DemoScript from "./DemoScript";
import { copyText } from "../utils/clipboard";

function prepText(p) {
  const out = ["INTRODUCTION", p.introduction_spoken || "", ""];
  if (p.best_match_project) out.push(`Q: ${p.best_match_project.question}`, p.best_match_project.spoken_answer, "");
  out.push("EXPERIENCE");
  (p.experience_walkthrough || []).forEach((r) => out.push(`- ${r.role}: ${r.spoken_answer}`));
  out.push("", "LIKELY QUESTIONS");
  (p.likely_questions || []).forEach((q) => out.push(`Q: ${q.question}`, `A: ${q.spoken_answer}`, ""));
  return out.join("\n");
}

function CopyBtn({ id, text, copied, onCopy, label = "📋 Copy" }) {
  return (
    <button type="button" className="small" onClick={() => onCopy(id, text)}>
      {copied === id ? "✓ Copied" : label}
    </button>
  );
}

export default function DemoPrepModal({ meetingId, onClose }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [generating, setGenerating] = useState(false);
  const [copied, setCopied] = useState("");
  const [zoom, setZoom] = useState(null);
  const [mode, setMode] = useState("script");

  useEffect(() => {
    api.getDemoPrep(meetingId).then(setData).catch((e) => setErr(e.message));
  }, [meetingId]);

  const generate = async () => {
    setErr("");
    setGenerating(true);
    try {
      setData(await api.generateDemoPrep(meetingId));
    } catch (e) {
      setErr(e.message);
    } finally {
      setGenerating(false);
    }
  };

  const copy = (key, text) => {
    copyText(text || "");
    setCopied(key);
    setTimeout(() => setCopied(""), 1500);
  };

  const p = data?.prep;
  const isNew = p && (p.introduction_spoken || p.experience_walkthrough);
  const docs = (data?.documents || data?.current_documents || []).map((d) => d.filename).join(", ");
  const cp = { copied, onCopy: copy };
  const hasScript = isNew && data?.script?.length > 0;
  const showScript = hasScript && mode === "script";

  return (
    <div className="diagram-modal-backdrop" onClick={onClose}>
      <div className="demo-prep-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Demo prep">
        <div className="diagram-modal-header">
          <strong>🎯 Demo Prep</strong>
          <div className="prep-header-actions">
            {hasScript && (
              <div className="seg" role="tablist" aria-label="View">
                <button type="button" role="tab" aria-selected={mode === "script"} className={mode === "script" ? "on" : ""} onClick={() => setMode("script")}>
                  Script
                </button>
                <button type="button" role="tab" aria-selected={mode === "full"} className={mode === "full" ? "on" : ""} onClick={() => setMode("full")}>
                  Full view
                </button>
              </div>
            )}
            <button type="button" className="small btn-demo-prep" disabled={generating} onClick={generate}>
              {generating ? "Generating… (~1–2 min)" : isNew ? "🔄 Regenerate" : "✨ Generate"}
            </button>
            {isNew && <CopyBtn id="all" text={prepText(p)} label="📄 Copy all" {...cp} />}
            <button type="button" className="small btn-close-modal" onClick={onClose}>
              ✕
            </button>
          </div>
        </div>

        {showScript && <DemoScript steps={data.script} />}
        <div className="demo-prep-body" hidden={showScript}>
          {err && (
            <div className="error-box" role="alert">
              {err}
            </div>
          )}
          {!data && !err && <p className="muted">Loading…</p>}
          {data && !isNew && (
            <div className="prep-card">
              <p>
                {p ? "This Demo Prep was made with the older format. " : "No Demo Prep yet. "}
                Click <b>Generate</b> — it reads your selected documents ({docs || "upload a CV and JD first"}) and
                builds read-aloud answers for every role and project, matched to the job description, plus a proposed
                architecture, database design and example code.
              </p>
            </div>
          )}

          {isNew && (
            <>
              <div className={`prep-source ${data.status === "stale" ? "stale" : ""}`}>
                {data.status === "stale" && "⚠ Documents changed since this was generated — regenerate. "}
                Generated {new Date(data.generated_at).toLocaleString()} with {data.model} from {docs}.
              </div>

              <section className="prep-card prep-confirmed">
                <div className="prep-card-header">
                  <span className="prep-badge badge-supported">FROM YOUR CV</span>
                  <strong>1. Spoken introduction</strong>
                  <CopyBtn id="intro" text={p.introduction_spoken} {...cp} />
                </div>
                <p className="prep-spoken-text">{p.introduction_spoken}</p>
              </section>

              {p.project_understanding?.spoken_answer && (
                <section className="prep-card prep-proposed">
                  <div className="prep-card-header">
                    <strong>How I understand your project</strong>
                    <CopyBtn id="pu" text={p.project_understanding.spoken_answer} {...cp} />
                  </div>
                  <p className="prep-spoken-text">{p.project_understanding.spoken_answer}</p>
                  <ul className="prep-list">
                    {(p.project_understanding.key_points || []).map((x, i) => (
                      <li key={i}>{x}</li>
                    ))}
                  </ul>
                </section>
              )}

              {p.best_match_project && (
                <section className="prep-card prep-confirmed">
                  <div className="prep-card-header">
                    <span className="prep-badge badge-supported">FROM YOUR CV</span>
                    <strong>2. “{p.best_match_project.question}”</strong>
                    <CopyBtn id="best" text={p.best_match_project.spoken_answer} {...cp} />
                  </div>
                  <p className="prep-spoken-text">{p.best_match_project.spoken_answer}</p>
                  <ul className="prep-list">
                    {(p.best_match_project.highlights || []).map((h, i) => (
                      <li key={i}>{h}</li>
                    ))}
                  </ul>
                  {p.best_match_project.source && <p className="prep-note">Source: {p.best_match_project.source}</p>}
                </section>
              )}

              {p.experience_walkthrough?.length > 0 && (
                <section className="prep-card prep-confirmed">
                  <div className="prep-card-header">
                    <strong>3. Experience walkthrough — every role</strong>
                  </div>
                  <div className="prep-qa-grid">
                    {p.experience_walkthrough.map((r, i) => (
                      <div key={i} className="prep-qa-item">
                        <div className="prep-qa-top">
                          <strong>{r.role}</strong>
                          <CopyBtn id={`role${i}`} text={r.spoken_answer} label="📋" {...cp} />
                        </div>
                        <p>{r.spoken_answer}</p>
                        <ul className="prep-list">
                          {(r.highlights || []).map((h, j) => (
                            <li key={j}>{h}</li>
                          ))}
                        </ul>
                        {r.jd_match?.length > 0 && <p className="prep-evidence">Matches JD: {r.jd_match.join(" · ")}</p>}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {p.projects?.length > 0 && (
                <section className="prep-card prep-confirmed">
                  <div className="prep-card-header">
                    <strong>4. Projects on your CV</strong>
                  </div>
                  <div className="prep-qa-grid">
                    {p.projects.map((pr, i) => (
                      <div key={i} className="prep-qa-item">
                        <div className="prep-qa-top">
                          <strong>{pr.title}</strong>
                          <CopyBtn id={`proj${i}`} text={pr.spoken_answer} label="📋" {...cp} />
                        </div>
                        <p>{pr.spoken_answer}</p>
                        {(pr.diagrams?.length ? pr.diagrams : pr.diagram ? [pr.diagram] : []).map((d, k) => (
                          <div key={k}>
                            {d.status && <p><strong style={{ color: d.status === "Proposed" ? "#b45309" : "#15803d" }}>[{d.status}]</strong> {d.title}</p>}
                            <DiagramView diagram={d} onOpenZoom={setZoom} />
                          </div>
                        ))}
                        {pr.system_design && <p><strong>System design:</strong> {pr.system_design}</p>}
                        {pr.database_design?.spoken && <p><strong>Database design:</strong> {pr.database_design.spoken}</p>}
                        {pr.database_design?.tables?.length > 0 && <pre className="prep-evidence">{pr.database_design.tables.join(String.fromCharCode(10))}</pre>}
                        {pr.aws_flow?.length > 0 && (
                          <ol className="prep-evidence">{pr.aws_flow.map((s, k) => <li key={k}>{s}</li>)}</ol>
                        )}
                        {pr.jd_relevance && <p className="prep-evidence">JD relevance: {pr.jd_relevance}</p>}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {p.jd_alignment?.length > 0 && (
                <section className="prep-card prep-boundary">
                  <div className="prep-card-header">
                    <strong>5. JD requirements → how to talk about each</strong>
                  </div>
                  <ul className="prep-align">
                    {p.jd_alignment.map((a, i) => {
                      return (
                        <li key={i}>
                          <div>
                            <b>{a.requirement}</b>
                            {a.evidence && <div className="prep-evidence">{a.evidence}</div>}
                            {a.how_to_talk_about_it && <div className="prep-spoken-inline">🗣 {a.how_to_talk_about_it}</div>}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              )}

              {p.proposed_architecture && (
                <section className="prep-card prep-proposed">
                  <div className="prep-card-header">
                    <span className="prep-badge badge-proposed">PROPOSED DESIGN</span>
                    <strong>6. {p.proposed_architecture.title}</strong>
                    <CopyBtn id="arch" text={p.proposed_architecture.spoken_summary} {...cp} />
                  </div>
                  <p className="prep-spoken-text">{p.proposed_architecture.spoken_summary}</p>
                  {p.proposed_architecture.diagram && (
                    <DiagramView diagram={p.proposed_architecture.diagram} onOpenZoom={setZoom} />
                  )}
                  {p.proposed_architecture.how_to_explain?.length > 0 && (
                    <>
                      <h4>How to explain it</h4>
                      <ol className="prep-list">
                        {p.proposed_architecture.how_to_explain.map((s, i) => (
                          <li key={i}>{s}</li>
                        ))}
                      </ol>
                    </>
                  )}
                  {p.proposed_architecture.assumptions?.length > 0 && (
                    <>
                      <h4>Assumptions to state</h4>
                      <ul className="prep-list">
                        {p.proposed_architecture.assumptions.map((s, i) => (
                          <li key={i}>{s}</li>
                        ))}
                      </ul>
                    </>
                  )}
                </section>
              )}

              {p.proposed_database_model && (
                <section className="prep-card prep-proposed">
                  <div className="prep-card-header">
                    <span className="prep-badge badge-proposed">PROPOSED SCHEMA</span>
                    <strong>7. {p.proposed_database_model.title}</strong>
                    <CopyBtn id="db" text={p.proposed_database_model.spoken_summary} {...cp} />
                  </div>
                  <p className="prep-spoken-text">{p.proposed_database_model.spoken_summary}</p>
                  {p.proposed_database_model.diagram && (
                    <DiagramView diagram={p.proposed_database_model.diagram} onOpenZoom={setZoom} />
                  )}
                  {p.proposed_database_model.ddl && <CodeBlockView lang="sql" code={p.proposed_database_model.ddl} />}
                </section>
              )}

              {p.example_code?.code && (
                <section className="prep-card prep-proposed">
                  <div className="prep-card-header">
                    <span className="prep-badge badge-proposed">EXAMPLE CODE</span>
                    <strong>8. {p.example_code.title}</strong>
                  </div>
                  {p.example_code.explanation && <p className="prep-spoken-text">{p.example_code.explanation}</p>}
                  <CodeBlockView lang={p.example_code.language || "python"} code={p.example_code.code} />
                </section>
              )}

              {p.likely_questions?.length > 0 && (
                <section className="prep-card">
                  <div className="prep-card-header">
                    <span className="prep-badge badge-skills">READ-ALOUD ANSWERS</span>
                    <strong>9. Likely client questions</strong>
                  </div>
                  <div className="prep-qa-grid">
                    {p.likely_questions.map((q, i) => (
                      <div key={i} className="prep-qa-item">
                        <div className="prep-qa-top">
                          <strong>
                            Q{i + 1}: {q.question}
                          </strong>
                          <CopyBtn id={`q${i}`} text={q.spoken_answer} label="📋" {...cp} />
                        </div>
                        <p>{q.spoken_answer}</p>
                        {q.evidence && <p className="prep-evidence">Based on: {q.evidence}</p>}
                        {q.gap && <p className="prep-note">Tip: {q.gap}</p>}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {p.prepared_topics?.length > 0 && (
                <section className="prep-card prep-confirmed">
                  <div className="prep-card-header">
                    <span className="prep-badge badge-skills">READ-ALOUD ANSWERS</span>
                    <strong>10. Prepared topics</strong>
                  </div>
                  <div className="prep-qa-grid">
                    {p.prepared_topics.map((t, i) => (
                      <div key={i} className="prep-qa-item">
                        <div className="prep-qa-top">
                          <strong>{t.question || t.topic}</strong>
                          <CopyBtn id={`t${i}`} text={t.spoken_answer} label="📋" {...cp} />
                        </div>
                        <p>{t.spoken_answer}</p>
                        {t.how_to_explain?.length > 0 && (
                          <ol className="prep-list">
                            {t.how_to_explain.map((x, j) => (
                              <li key={j}>{x}</li>
                            ))}
                          </ol>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </>
          )}
        </div>
      </div>
      {zoom && (
        <div onClick={(e) => e.stopPropagation()}>
          <DiagramZoomModal diagram={zoom} onClose={() => setZoom(null)} />
        </div>
      )}
    </div>
  );
}

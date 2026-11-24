import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { marked } from "marked";
import { parseStructuredAnswerClient } from "../utils/answerParser";
import { copyText } from "../utils/clipboard";

const DIAGRAM_TOKEN_RE = /\[\[DIAGRAM_(\d+)\]\]/g;

function decodeEntities(str) {
  if (!str) return "";
  return str
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

function isSafeUrl(href) {
  if (!href) return false;
  try {
    const u = new URL(href, window.location.origin);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

const CODE_TOKEN_RE = new RegExp(
  [
    "(?<comment>#[^\\n]*|--[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/)",
    "(?<string>\"\"\"[\\s\\S]*?\"\"\"|'''[\\s\\S]*?'''|f?\"(?:\\\\.|[^\"\\\\\\n])*\"|f?'(?:\\\\.|[^'\\\\\\n])*')",
    "(?<decorator>@[A-Za-z_][A-Za-z0-9_.]*)",
    "(?<number>\\b\\d+(?:_\\d+)*(?:\\.\\d+)?\\b)",
    "(?<ident>\\b[A-Za-z_][A-Za-z0-9_]*\\b)",
    "(?<op>[=+\\-*/%<>!&|^~:;,.()[\\]{}]+)",
    "(?<ws>\\s+)",
  ].join("|"),
  "g"
);

const KW_SET = new Set([
  "def", "class", "import", "from", "as", "return", "yield", "if", "elif", "else",
  "for", "while", "in", "not", "and", "or", "is", "try", "except", "finally",
  "raise", "with", "async", "await", "lambda", "pass", "break", "continue",
  "global", "nonlocal", "assert", "del",
  "select", "where", "join", "left", "right", "inner", "outer", "full", "cross",
  "on", "group", "by", "order", "having", "limit", "offset", "insert", "into",
  "values", "update", "set", "delete", "merge", "using", "when", "matched",
  "then", "create", "alter", "drop", "table", "view", "index", "schema",
  "database", "primary", "foreign", "key", "references", "unique", "check",
  "default", "identity", "distkey", "sortkey", "diststyle", "compound",
  "interleaved", "all", "even", "auto", "encode", "copy", "unload", "iam_role",
  "format", "parquet", "csv", "json", "manifest", "statupdate", "compupdate",
  "region", "begin", "transaction", "commit", "rollback", "truncate", "analyze",
  "vacuum", "grant", "revoke", "distinct", "union", "case", "end", "over",
  "partition", "rows", "range", "between", "preceding", "following", "current", "row",
  "asc", "desc", "nulls", "first", "last",
]);

const CONST_SET = new Set([
  "true", "false", "none", "null", "off", "self", "cls",
]);

const TYPE_BUILTIN_SET = new Set([
  "int", "str", "float", "bool", "list", "dict", "set", "tuple", "bytes", "object",
  "exception", "filenotfounderror", "valueerror", "typeerror", "keyerror", "runtimeerror",
  "zerodivisionerror", "indexerror", "attributeerror", "oserror", "ioerror",
  "print", "open", "len", "range", "enumerate", "zip", "map", "filter", "sorted",
  "sum", "min", "max", "abs", "round", "isinstance", "getattr", "setattr", "hasattr",
  "super", "property", "staticmethod", "classmethod", "dataclass",
  "bigint", "integer", "smallint", "decimal", "numeric", "real", "double", "precision",
  "varchar", "char", "text", "timestamp", "timestamptz", "date", "time", "boolean",
  "super", "varbyte", "sparksession", "dataframe", "window", "deltatable",
  "col", "lit", "coalesce", "row_number", "rank", "dense_rank", "broadcast", "udf",
  "pd", "np", "spark", "df",
]);

function highlightCodeTokens(code) {
  if (!code) return null;
  const nodes = [];
  let lastIdx = 0;
  let match;
  CODE_TOKEN_RE.lastIndex = 0;
  let k = 0;

  while ((match = CODE_TOKEN_RE.exec(code)) !== null) {
    if (match.index > lastIdx) {
      nodes.push(code.slice(lastIdx, match.index));
    }
    const full = match[0];
    const groups = match.groups || {};
    let cls = "";

    if (groups.comment !== undefined) {
      cls = "tok-comment";
    } else if (groups.string !== undefined) {
      cls = "tok-string";
    } else if (groups.decorator !== undefined) {
      cls = "tok-decorator";
    } else if (groups.number !== undefined) {
      cls = "tok-number";
    } else if (groups.ident !== undefined) {
      const low = full.toLowerCase();
      // Look ahead for '(' to detect function/method calls
      const after = code.slice( match.index + full.length );
      if (KW_SET.has(low)) {
        cls = "tok-kw";
      } else if (CONST_SET.has(low)) {
        cls = "tok-const";
      } else if (TYPE_BUILTIN_SET.has(low)) {
        cls = "tok-builtin";
      } else if (/^\s*\(/.test(after)) {
        cls = "tok-fn";
      } else if (/^[A-Z][A-Za-z0-9_]*$/.test(full)) {
        cls = "tok-class";
      } else {
        cls = "tok-ident";
      }
    } else if (groups.op !== undefined) {
      cls = "tok-op";
    }

    if (cls) {
      nodes.push(
        <span key={`t-${k++}`} className={cls}>
          {full}
        </span>
      );
    } else {
      nodes.push(full);
    }
    lastIdx = match.index + full.length;
  }
  if (lastIdx < code.length) {
    nodes.push(code.slice(lastIdx));
  }
  return nodes;
}

export function CodeBlockView({ lang, code }) {
  const [copied, setCopied] = useState(false);
  const highlighted = useMemo(() => highlightCodeTokens(code), [code]);
  const onCopy = () => {
    copyText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };
  return (
    <div className="code-block-wrap codesnap-card">
      <div className="code-block-header">
        <div className="codesnap-header-left">
          <span className="codesnap-dots" aria-hidden="true">
            <span className="codesnap-dot dot-red" />
            <span className="codesnap-dot dot-yellow" />
            <span className="codesnap-dot dot-green" />
          </span>
          <span className="code-lang">{lang || "code"}</span>
        </div>
        <button type="button" className="small btn-copy-code-inline" onClick={onCopy}>
          {copied ? "✓ Copied" : "📋 Copy code"}
        </button>
      </div>
      <pre className="code-pre">
        <code>{highlighted}</code>
      </pre>
    </div>
  );
}

export function DiagramView({ diagram, onOpenZoom }) {
  const [copiedSrc, setCopiedSrc] = useState(false);
  if (!diagram) return null;

  const copySource = () => {
    copyText(diagram.source || "");
    setCopiedSrc(true);
    setTimeout(() => setCopiedSrc(false), 1600);
  };

  const downloadSvg = () => {
    if (!diagram.svg) return;
    const blob = new Blob([diagram.svg], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `diagram-${(diagram.index || 0) + 1}.svg`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const svgDataUri = useMemo(() => {
    if (!diagram.ok || !diagram.svg) return "";
    return `data:image/svg+xml;utf8,${encodeURIComponent(diagram.svg)}`;
  }, [diagram.ok, diagram.svg]);

  return (
    <div className="diagram-card" data-testid={`diagram-card-${diagram.index}`}>
      <div className="diagram-toolbar">
        <span className="diagram-title">
          {diagram.kind === "erDiagram"
            ? `🗄️ ER Diagram #${(diagram.index || 0) + 1}`
            : `📊 Architecture Flow #${(diagram.index || 0) + 1}`}
        </span>
        <div className="diagram-actions">
          {diagram.ok && diagram.svg && (
            <>
              <button
                type="button"
                className="small btn-diagram-zoom"
                onClick={() => onOpenZoom?.(diagram)}
              >
                🔍 Expand / Zoom
              </button>
              <button type="button" className="small" onClick={downloadSvg}>
                💾 SVG
              </button>
            </>
          )}
          <button type="button" className="small" onClick={copySource}>
            {copiedSrc ? "✓ Copied" : "📋 Source"}
          </button>
        </div>
      </div>

      {diagram.ok && svgDataUri ? (
        <div
          className="diagram-canvas-fit"
          onClick={() => onOpenZoom?.(diagram)}
          title="Click to expand and zoom diagram"
        >
          <img
            src={svgDataUri}
            alt={`Diagram ${(diagram.index || 0) + 1}`}
            className="diagram-svg-img"
          />
        </div>
      ) : (
        <div className="diagram-fallback">
          <div className="diagram-fallback-header">
            ⚠️ Diagram fallback ({diagram.error || "Unrecognized diagram syntax"})
          </div>
          <pre className="diagram-fallback-pre">{diagram.source}</pre>
        </div>
      )}
    </div>
  );
}

function renderInlineTokens(tokens, keyPrefix = "inl") {
  if (!tokens || !tokens.length) return null;
  return tokens.map((t, i) => {
    const key = `${keyPrefix}-${i}`;
    switch (t.type) {
      case "strong":
        return <strong key={key}>{renderInlineTokens(t.tokens, key)}</strong>;
      case "em":
        return <em key={key}>{renderInlineTokens(t.tokens, key)}</em>;
      case "codespan":
        return (
          <code key={key} className="inline-code">
            {decodeEntities(t.text)}
          </code>
        );
      case "br":
        return <br key={key} />;
      case "del":
        return <del key={key}>{renderInlineTokens(t.tokens, key)}</del>;
      case "link":
        if (isSafeUrl(t.href)) {
          return (
            <a key={key} href={t.href} target="_blank" rel="noopener noreferrer">
              {renderInlineTokens(t.tokens, key) || t.text}
            </a>
          );
        }
        return <span key={key}>{t.text} (unsafe link blocked)</span>;
      case "image":
        return <span key={key}>[remote image blocked: {t.text || "image"}]</span>;
      case "html":
        return <span key={key}>{t.raw || t.text}</span>;
      case "escape":
        return <span key={key}>{t.text}</span>;
      case "text":
      default:
        if (t.tokens && t.tokens.length) {
          return <span key={key}>{renderInlineTokens(t.tokens, key)}</span>;
        }
        return <span key={key}>{decodeEntities(t.text ?? t.raw ?? "")}</span>;
    }
  });
}

function SafeMarkdown({ markdown, diagrams = [], onOpenZoom }) {
  const blocks = useMemo(() => {
    if (!markdown) return [];
    const parts = [];
    let lastIndex = 0;
    let match;
    DIAGRAM_TOKEN_RE.lastIndex = 0;
    while ((match = DIAGRAM_TOKEN_RE.exec(markdown)) !== null) {
      const before = markdown.slice(lastIndex, match.index).trim();
      if (before) parts.push({ kind: "md", text: before });
      parts.push({ kind: "diagram", index: parseInt(match[1], 10) });
      lastIndex = match.index + match[0].length;
    }
    const tail = markdown.slice(lastIndex).trim();
    if (tail) parts.push({ kind: "md", text: tail });
    return parts;
  }, [markdown]);

  if (!blocks.length) return null;

  return (
    <div className="safe-markdown">
      {blocks.map((b, bIdx) => {
        if (b.kind === "diagram") {
          const diag = diagrams.find((d) => d.index === b.index) || diagrams[b.index];
          if (!diag) {
            return (
              <div key={`diag-wait-${bIdx}`} className="diagram-loading">
                *(Rendering diagram #{b.index + 1}…)*
              </div>
            );
          }
          return <DiagramView key={`diag-${b.index}-${bIdx}`} diagram={diag} onOpenZoom={onOpenZoom} />;
        }

        let tokens = [];
        try {
          tokens = marked.lexer(b.text, { gfm: true, breaks: true });
        } catch {
          return <p key={`raw-${bIdx}`}>{b.text}</p>;
        }

        return tokens.map((tok, tIdx) => {
          const key = `blk-${bIdx}-${tIdx}`;
          switch (tok.type) {
            case "space":
              return null;
            case "hr":
              return <hr key={key} className="md-hr" />;
            case "heading": {
              const Tag = `h${Math.min(4, Math.max(1, tok.depth || 3))}`;
              return (
                <Tag key={key} className={`md-h md-h${tok.depth || 3}`}>
                  {renderInlineTokens(tok.tokens, key)}
                </Tag>
              );
            }
            case "paragraph":
              return (
                <p key={key} className="md-p">
                  {renderInlineTokens(tok.tokens, key)}
                </p>
              );
            case "blockquote":
              return (
                <blockquote key={key} className="md-quote">
                  <SafeMarkdown
                    markdown={tok.text || ""}
                    diagrams={diagrams}
                    onOpenZoom={onOpenZoom}
                  />
                </blockquote>
              );
            case "list": {
              const ListTag = tok.ordered ? "ol" : "ul";
              return (
                <ListTag key={key} className="md-list" start={tok.start || undefined}>
                  {(tok.items || []).map((item, liIdx) => (
                    <li key={`${key}-li-${liIdx}`} className="md-li">
                      {item.tokens ? renderListItemTokens(item.tokens, `${key}-li-${liIdx}`) : decodeEntities(item.text)}
                    </li>
                  ))}
                </ListTag>
              );
            }
            case "code":
              return <CodeBlockView key={key} lang={tok.lang} code={tok.text} />;
            case "table":
              return (
                <div key={key} className="md-table-scroll">
                  <table className="md-table">
                    <thead>
                      <tr>
                        {(tok.header || []).map((h, hIdx) => (
                          <th key={`${key}-th-${hIdx}`}>
                            {renderInlineTokens(h.tokens, `${key}-th-${hIdx}`)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(tok.rows || []).map((row, rIdx) => (
                        <tr key={`${key}-tr-${rIdx}`}>
                          {row.map((cell, cIdx) => (
                            <td key={`${key}-td-${rIdx}-${cIdx}`}>
                              {renderInlineTokens(cell.tokens, `${key}-td-${rIdx}-${cIdx}`)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              );
            case "html":
              return (
                <p key={key} className="md-p md-escaped-html">
                  {tok.raw || tok.text}
                </p>
              );
            default:
              if (tok.tokens) {
                return (
                  <p key={key} className="md-p">
                    {renderInlineTokens(tok.tokens, key)}
                  </p>
                );
              }
              return tok.text ? (
                <p key={key} className="md-p">
                  {decodeEntities(tok.text)}
                </p>
              ) : null;
          }
        });
      })}
    </div>
  );
}

function renderListItemTokens(tokens, keyPrefix) {
  return tokens.map((t, idx) => {
    const k = `${keyPrefix}-${idx}`;
    if (t.type === "text" && t.tokens) {
      return <span key={k}>{renderInlineTokens(t.tokens, k)}</span>;
    }
    if (t.type === "paragraph" && t.tokens) {
      return <span key={k}>{renderInlineTokens(t.tokens, k)}</span>;
    }
    if (t.type === "list") {
      const SubTag = t.ordered ? "ol" : "ul";
      return (
        <SubTag key={k} className="md-list">
          {(t.items || []).map((sub, sIdx) => (
            <li key={`${k}-s-${sIdx}`}>
              {sub.tokens ? renderListItemTokens(sub.tokens, `${k}-s-${sIdx}`) : decodeEntities(sub.text)}
            </li>
          ))}
        </SubTag>
      );
    }
    if (t.type === "code") {
      return <CodeBlockView key={k} lang={t.lang} code={t.text} />;
    }
    return renderInlineTokens([t], k);
  });
}

function CollapsibleDrawer({ title, badge, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="technical-details-section">
      <button
        type="button"
        className="tech-toggle-btn"
        onClick={() => setOpen((v) => !v)}
      >
        <span>
          {open ? "▾" : "▸"} {title}
          {badge ? <span className="drawer-badge">{badge}</span> : null}
        </span>
        <span className="tech-toggle-hint">{open ? "Hide" : "Show"}</span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.14 }}
            className="technical-details-body"
          >
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function AnswerCard({ answer, onOpenZoom, spokenFontSize, onAdjustFontSize }) {
  const parsed = useMemo(() => parseStructuredAnswerClient(answer), [answer]);
  const [copiedKey, setCopiedKey] = useState(null);

  const isStreaming = answer.status === "streaming";
  const isFailed = answer.status === "failed";

  // Partition diagrams, SQL DDL, implementation prose, and code blocks into the 4 clean drawers
  const archDiagrams = useMemo(
    () => (parsed.diagrams || []).filter((d) => d.kind !== "erDiagram"),
    [parsed.diagrams]
  );
  const erDiagrams = useMemo(
    () => (parsed.diagrams || []).filter((d) => d.kind === "erDiagram"),
    [parsed.diagrams]
  );
  const sqlBlocks = useMemo(
    () =>
      (parsed.code_blocks || []).filter(
        (c) => (c.language || "").toLowerCase() === "sql"
      ),
    [parsed.code_blocks]
  );
  const exampleCodeBlocks = useMemo(
    () =>
      (parsed.code_blocks || []).filter(
        (c) => (c.language || "").toLowerCase() !== "sql"
      ),
    [parsed.code_blocks]
  );

  // Strip [[DIAGRAM_N]] tokens and fenced code blocks from implementation markdown so they aren't duplicated
  const implMarkdown = useMemo(() => {
    if (!parsed.technical_markdown) return "";
    return parsed.technical_markdown
      .replace(/\[\[DIAGRAM_\d+\]\]/g, "")
      .replace(/```[\s\S]*?```/g, "")
      .trim();
  }, [parsed.technical_markdown]);

  const hasArchDrawer = Boolean(
    archDiagrams.length > 0 ||
      (parsed.how_to_draw_markdown && parsed.how_to_draw_markdown.trim())
  );
  const hasDbDrawer = Boolean(erDiagrams.length > 0 || sqlBlocks.length > 0);
  const hasImplDrawer = Boolean(implMarkdown);
  const hasCodeDrawer = Boolean(
    exampleCodeBlocks.length > 0 || (!hasDbDrawer && sqlBlocks.length > 0)
  );

  const copyWithFeedback = (key, text) => {
    if (!text) return;
    copyText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 1600);
  };

  const fullCleanText = useMemo(() => {
    return (answer.answer_text || "")
      .replace(/```design-state[\s\S]*?```/gi, "")
      .trim();
  }, [answer.answer_text]);

  return (
    <motion.article
      layout="position"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      className="answer-card"
      data-testid="answer-card"
    >
      {/* 1. Question Header */}
      <div className="question-section">
        <div className="question-top-row">
          <span className="section-micro-label">Question</span>
          <span className={`status-pill status-${answer.status || "completed"}`}>
            {isStreaming ? "Streaming…" : isFailed ? "Failed" : "Ready"}
            {!isStreaming && !isFailed && answer.total_ms
              ? ` · ${(answer.total_ms / 1000).toFixed(1)}s (first words ${(answer.first_word_ms / 1000).toFixed(1)}s)`
              : ""}
          </span>
        </div>
        <p className="q">{answer.question_text}</p>
      </div>

      {/* 2. Suggested Spoken Answer (Teleprompter View) */}
      <div
        className="spoken-answer-box"
        style={{ "--spoken-font-size": `${spokenFontSize}px` }}
      >
        <div className="spoken-answer-label">Suggested answer</div>
        {!parsed.spoken_markdown ? (
          isFailed ? (
            <p className="error">Could not generate an answer.</p>
          ) : (
            <p className="muted">Generating answer… ▌</p>
          )
        ) : (
          <div className="spoken-teleprompter-text">
            <SafeMarkdown
              markdown={parsed.spoken_markdown}
              diagrams={parsed.diagrams}
              onOpenZoom={onOpenZoom}
            />
          </div>
        )}
        {isStreaming && <span className="cursor">▌</span>}
      </div>

      {/* 3. Teleprompter Action Bar: [Copy answer]  [A-] [A+] */}
      <div className="teleprompter-toolbar">
        <div className="teleprompter-left-actions">
          <button
            type="button"
            className="small btn-copy-spoken"
            onClick={() =>
              copyWithFeedback("spoken", parsed.spoken_clean || parsed.spoken_markdown)
            }
            disabled={!parsed.spoken_markdown}
            title="Copy natural spoken answer for reading or sharing"
          >
            {copiedKey === "spoken" ? "✓ Copied answer" : "📋 Copy answer"}
          </button>
          <button
            type="button"
            className="small btn-copy-answer"
            onClick={() => copyWithFeedback("full", fullCleanText)}
            disabled={!fullCleanText}
            title="Copy complete answer including technical sections"
          >
            {copiedKey === "full" ? "✓ Copied full" : "📄 Copy full"}
          </button>
        </div>
        <div className="teleprompter-font-controls" aria-label="Reading font size controls">
          <button
            type="button"
            className="small btn-font-step"
            onClick={() => onAdjustFontSize(-1)}
            title="Decrease reading text size (A−)"
          >
            A−
          </button>
          <button
            type="button"
            className="small btn-font-step"
            onClick={() => onAdjustFontSize(1)}
            title="Increase reading text size (A+)"
          >
            A+
          </button>
        </div>
      </div>

      {/* 4. Structured Collapsible Drawers */}
      <div className="structured-drawers">
        {hasArchDrawer && (
          <CollapsibleDrawer
            title="Architecture diagram"
            badge={archDiagrams.length ? `${archDiagrams.length} SVG` : null}
            defaultOpen={false}
          >
            {archDiagrams.map((d) => (
              <DiagramView key={`arch-${d.index}`} diagram={d} onOpenZoom={onOpenZoom} />
            ))}
            {parsed.how_to_draw_markdown && (
              <div className="how-to-draw-box" style={{ marginTop: "8px" }}>
                <div className="how-to-draw-label">✏️ HOW TO EXPLAIN / DRAW ON WHITEBOARD</div>
                <SafeMarkdown
                  markdown={parsed.how_to_draw_markdown}
                  diagrams={parsed.diagrams}
                  onOpenZoom={onOpenZoom}
                />
              </div>
            )}
          </CollapsibleDrawer>
        )}

        {hasDbDrawer && (
          <CollapsibleDrawer
            title="Database design"
            badge={
              erDiagrams.length && sqlBlocks.length
                ? "ER + DDL"
                : erDiagrams.length
                ? "ER Diagram"
                : "SQL DDL"
            }
            defaultOpen={false}
          >
            {erDiagrams.map((d) => (
              <DiagramView key={`er-${d.index}`} diagram={d} onOpenZoom={onOpenZoom} />
            ))}
            {sqlBlocks.map((b, idx) => (
              <CodeBlockView key={`sql-${idx}`} lang={b.language || "sql"} code={b.code} />
            ))}
          </CollapsibleDrawer>
        )}

        {hasImplDrawer && (
          <CollapsibleDrawer
            title="Implementation details"
            badge="SOLID & Trade-offs"
            defaultOpen={false}
          >
            <SafeMarkdown
              markdown={implMarkdown}
              diagrams={parsed.diagrams}
              onOpenZoom={onOpenZoom}
            />
          </CollapsibleDrawer>
        )}

        {hasCodeDrawer && (
          <CollapsibleDrawer
            title="Example code"
            badge={`${(exampleCodeBlocks.length ? exampleCodeBlocks : sqlBlocks)
              .map((c) => c.language || "code")
              .join(", ")}`}
            defaultOpen={false}
          >
            {(exampleCodeBlocks.length ? exampleCodeBlocks : sqlBlocks).map((b, idx) => (
              <CodeBlockView key={`code-${idx}`} lang={b.language || "python"} code={b.code} />
            ))}
          </CollapsibleDrawer>
        )}
      </div>

      {/* Grounded workspace documents */}
      {answer.sources?.some((s) => s.source_type === "document") && (
        <div className="doc-sources-bar">
          📎 Grounded in documents:{" "}
          {answer.sources
            .filter((s) => s.source_type === "document")
            .map((s) => s.label)
            .join(", ")}
        </div>
      )}
    </motion.article>
  );
}

function PanZoomImage({ src, scale, setScale, resetKey, command }) {
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const areaRef = useRef(null);
  const imgRef = useRef(null);

  // Place the diagram at readable (natural) size, left edge in view; "fit" shrinks it to the whole width.
  const place = useCallback((mode) => {
    const area = areaRef.current, img = imgRef.current;
    if (!area || !img || !img.naturalWidth) return;
    const C = area.clientWidth, H = area.clientHeight, W = img.naturalWidth, Hn = img.naturalHeight;
    if (mode === "fit") {
      const s = Math.min(C / W, H / Hn) * 0.96;
      setScale(s); setPos({ x: 0, y: 0 });
    } else {
      setScale(1);
      setPos({ x: W > C ? (W - C) / 2 + 12 : 0, y: 0 });
    }
  }, [setScale]);

  useEffect(() => { if (command) place(command.mode); }, [command, place]);
  const ptrs = useRef(new Map());
  const gesture = useRef(null);


  const clamp = (s) => Math.min(6, Math.max(0.3, s));
  const dist = () => {
    const [p, q] = [...ptrs.current.values()];
    return Math.hypot(p.x - q.x, p.y - q.y);
  };

  const onPointerDown = (e) => {
    e.currentTarget.setPointerCapture?.(e.pointerId);
    ptrs.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    gesture.current = ptrs.current.size === 2
      ? { type: "pinch", d: dist(), s: scale }
      : { type: "pan", x: e.clientX, y: e.clientY, px: pos.x, py: pos.y };
  };
  const onPointerMove = (e) => {
    if (!ptrs.current.has(e.pointerId)) return;
    ptrs.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const g = gesture.current;
    if (!g) return;
    if (g.type === "pinch" && ptrs.current.size >= 2) {
      setScale(clamp(g.s * (dist() / g.d)));
    } else if (g.type === "pan") {
      setPos({ x: g.px + (e.clientX - g.x), y: g.py + (e.clientY - g.y) });
    }
  };
  const onPointerUp = (e) => {
    ptrs.current.delete(e.pointerId);
    const rest = [...ptrs.current.values()][0];
    gesture.current = rest ? { type: "pan", x: rest.x, y: rest.y, px: pos.x, py: pos.y } : null;
  };
  const onWheel = (e) => {
    setScale((s) => clamp(s * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
  };

  return (
    <div
      className="diagram-pan-area"
      ref={areaRef}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onWheel={onWheel}
      onDoubleClick={() => place("read")}
    >
      <img
        ref={imgRef}
        src={src}
        alt="Expanded diagram"
        draggable={false}
        onLoad={() => place("read")}
        style={{ transform: `translate(${pos.x}px, ${pos.y}px) scale(${scale})` }}
      />
    </div>
  );
}

export function DiagramZoomModal({ diagram, onClose }) {
  const [scale, setScale] = useState(1);
  const [command, setCommand] = useState(null);
  if (!diagram) return null;

  const svgDataUri = `data:image/svg+xml;utf8,${encodeURIComponent(diagram.svg || "")}`;

  return (
    <div className="diagram-modal-backdrop" onClick={onClose}>
      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.16 }}
        className="diagram-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="diagram-modal-header">
          <strong>
            {diagram.kind === "erDiagram" ? "Entity-Relationship Diagram" : "Architecture Diagram"}
          </strong>
          <div className="diagram-modal-controls">
            <button type="button" className="small" onClick={() => setScale((z) => Math.max(0.3, z / 1.25))}>
              −
            </button>
            <span className="zoom-pct">{Math.round(scale * 100)}%</span>
            <button type="button" className="small" onClick={() => setScale((z) => Math.min(6, z * 1.25))}>
              +
            </button>
            <button type="button" className="small" onClick={() => setCommand({ mode: "read" })}>
              Readable
            </button>
            <button type="button" className="small" onClick={() => setCommand({ mode: "fit" })}>
              Fit all
            </button>
            <button type="button" className="small btn-close-modal" onClick={onClose}>
              ✕ Close
            </button>
          </div>
        </div>
        <div className="diagram-modal-body diagram-modal-pan">
          <PanZoomImage src={svgDataUri} scale={scale} setScale={setScale} resetKey={diagram.index} command={command} />
          <div className="diagram-hint">Drag to move · pinch or scroll to zoom · double-tap for readable size</div>
        </div>
      </motion.div>
    </div>
  );
}

export default function AnswerPanel({
  answers = [],
  expandedPane = null,
  onToggleExpand = null,
}) {
  const safeAnswers = answers || [];
  const scrollRef = useRef(null);
  const pinnedRef = useRef(true);
  const [showJump, setShowJump] = useState(false);
  const [zoomDiagram, setZoomDiagram] = useState(null);
  const [spokenFontSize, setSpokenFontSize] = useState(() => {
    const saved = parseInt(localStorage.getItem("ma_spoken_font_px") || "15", 10);
    return Number.isFinite(saved) ? Math.min(22, Math.max(13, saved)) : 15;
  });

  const adjustFontSize = useCallback((delta) => {
    setSpokenFontSize((prev) => {
      const next = Math.min(22, Math.max(13, prev + delta));
      localStorage.setItem("ma_spoken_font_px", String(next));
      return next;
    });
  }, []);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    const atBottom = distance <= 65;
    pinnedRef.current = atBottom;
    if (atBottom) setShowJump(false);
  }, []);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current = true;
    setShowJump(false);
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const hasSelection = Boolean(window.getSelection && window.getSelection().toString().trim());
    if (pinnedRef.current && !hasSelection) {
      el.scrollTop = el.scrollHeight;
    } else if (!pinnedRef.current && safeAnswers.length > 0) {
      setShowJump(true);
    }
  }, [safeAnswers]);

  const isExpanded = expandedPane === "answers";

  return (
    <section className="panel answer-panel-root">
      <header className="panel-header">
        <div className="panel-header-left">
          <h2>AI Answers</h2>
          <span className="muted" style={{ fontSize: "12px" }}>
            {safeAnswers.length} answer{safeAnswers.length === 1 ? "" : "s"}
          </span>
        </div>
        <div className="panel-header-actions">
          <button
            type="button"
            className="small btn-font-step"
            onClick={() => adjustFontSize(-1)}
            title="Decrease spoken answer font size"
          >
            A−
          </button>
          <button
            type="button"
            className="small btn-font-step"
            onClick={() => adjustFontSize(1)}
            title="Increase spoken answer font size"
          >
            A+
          </button>
          {onToggleExpand && (
            <button
              type="button"
              className="small desktop-only-btn"
              onClick={onToggleExpand}
              title={isExpanded ? "Restore 45/55 split" : "Expand AI Answers panel"}
            >
              {isExpanded ? "❐ Restore Split" : "◨ Expand Answer"}
            </button>
          )}
        </div>
      </header>
      <div className="panel-body" ref={scrollRef} onScroll={handleScroll}>
        {safeAnswers.length === 0 && (
          <p className="muted">
            Click <b>Send</b> on any transcript paragraph or type a question below to get a natural spoken answer, architecture diagram, or database design.
          </p>
        )}
        {safeAnswers.map((a) => (
          <AnswerCard
            key={a.id}
            answer={a}
            onOpenZoom={setZoomDiagram}
            spokenFontSize={spokenFontSize}
            onAdjustFontSize={adjustFontSize}
          />
        ))}
      </div>
      {showJump && (
        <button type="button" className="jump-latest-btn" onClick={scrollToBottom}>
          ↓ Jump to latest answer
        </button>
      )}
      <DiagramZoomModal diagram={zoomDiagram} onClose={() => setZoomDiagram(null)} />
    </section>
  );
}

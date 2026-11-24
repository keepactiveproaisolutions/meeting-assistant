/**
 * Client-side structured answer parser mirroring backend/app/services/diagram_renderer.py.
 * Splits streaming or completed answer text into:
 * - spoken_markdown / spoken_clean
 * - technical_markdown (with [[DIAGRAM_n]] placeholders)
 * - how_to_draw_markdown
 * - code_blocks
 * Holds back unclosed ```mermaid and ```design-state blocks while streaming.
 */

const DESIGN_STATE_RE = /```design-state\s*\r?\n([\s\S]*?)```/gi;
const MERMAID_CLOSED_RE = /```mermaid\s*\r?\n([\s\S]*?)```/gi;
const CODE_BLOCK_RE = /```([a-zA-Z0-9_-]*)\r?\n([\s\S]*?)```/g;

const TECH_SPLIT_RE =
  /(?:^|\n)#{1,3}\s*(?:B\.\s*)?(?:Technical\s+Details?|Architecture\s*(?:&|and)\s*Details?|Supporting\s+Details?)\s*(?:\n|$)/i;
const HOW_TO_DRAW_RE =
  /(?:^|\n)#{1,3}\s*(?:C\.\s*)?How\s+to\s+draw\s+it(?:\s+on\s+a\s+whiteboard)?\s*(?:\n|$)/i;
const SPOKEN_HEADER_RE =
  /^\s*#{1,3}\s*(?:A\.\s*)?(?:Suggested\s+spoken\s+answer|Spoken\s+answer)\s*\r?\n+/i;

export function stripMarkdownToPlain(md) {
  if (!md) return "";
  let s = md;
  s = s.replace(/\[\[DIAGRAM_\d+\]\]/g, "");
  s = s.replace(/```[\s\S]*?```/g, "");
  s = s.replace(/^#{1,6}\s+/gm, "");
  s = s.replace(/\*\*([^*]+)\*\*/g, "$1");
  s = s.replace(/\*([^*]+)\*/g, "$1");
  s = s.replace(/`([^`]+)`/g, "$1");
  s = s.replace(/\n{3,}/g, "\n\n");
  return s.trim();
}

export function parseStructuredAnswerClient(answerObj) {
  const rawText = answerObj?.answer_text || "";
  const isComplete = answerObj?.status === "completed";

  // If completed and backend already provided parsed fields, use them directly
  if (
    isComplete &&
    answerObj?.spoken_markdown !== undefined &&
    (answerObj.spoken_markdown || answerObj.technical_markdown)
  ) {
    return {
      spoken_markdown: answerObj.spoken_markdown || "",
      spoken_clean: answerObj.spoken_clean || stripMarkdownToPlain(answerObj.spoken_markdown || ""),
      technical_markdown: answerObj.technical_markdown || "",
      how_to_draw_markdown: answerObj.how_to_draw_markdown || "",
      diagrams: answerObj.diagrams || [],
      code_blocks: answerObj.code_blocks || [],
    };
  }

  let text = rawText.replace(DESIGN_STATE_RE, "");
  if (!isComplete) {
    const lower = text.toLowerCase();
    const dsIdx = lower.lastIndexOf("```design-state");
    if (dsIdx !== -1 && text.indexOf("```", dsIdx + 15) === -1) {
      text = text.slice(0, dsIdx);
    }
  }

  let diagIdx = 0;
  let cleaned = text.replace(MERMAID_CLOSED_RE, () => {
    const token = `\n\n[[DIAGRAM_${diagIdx}]]\n\n`;
    diagIdx += 1;
    return token;
  });

  if (!isComplete) {
    const lower = cleaned.toLowerCase();
    const openIdx = lower.lastIndexOf("```mermaid");
    if (openIdx !== -1 && cleaned.indexOf("```", openIdx + 10) === -1) {
      cleaned = cleaned.slice(0, openIdx) + "\n\n*(Rendering diagram…)*\n";
    }
  }

  const code_blocks = [];
  let match;
  CODE_BLOCK_RE.lastIndex = 0;
  while ((match = CODE_BLOCK_RE.exec(cleaned)) !== null) {
    const lang = (match[1] || "text").trim().toLowerCase();
    if (lang !== "mermaid" && lang !== "design-state") {
      code_blocks.push({ language: lang || "text", code: match[2].replace(/\r?\n$/, "") });
    }
  }

  let how_to_draw_markdown = "";
  const htdMatch = HOW_TO_DRAW_RE.exec(cleaned);
  let mainPart = cleaned;
  if (htdMatch) {
    mainPart = cleaned.slice(0, htdMatch.index);
    how_to_draw_markdown = cleaned.slice(htdMatch.index + htdMatch[0].length).trim();
  }

  let spoken_markdown = "";
  let technical_markdown = "";
  const techMatch = TECH_SPLIT_RE.exec(mainPart);
  if (techMatch) {
    spoken_markdown = mainPart.slice(0, techMatch.index).trim();
    technical_markdown = mainPart.slice(techMatch.index + techMatch[0].length).trim();
  } else {
    spoken_markdown = mainPart.trim();
  }

  spoken_markdown = spoken_markdown.replace(SPOKEN_HEADER_RE, "").trim();

  if (spoken_markdown.includes("[[DIAGRAM_") && !technical_markdown) {
    const firstDiag = spoken_markdown.indexOf("[[DIAGRAM_");
    technical_markdown = spoken_markdown.slice(firstDiag).trim();
    spoken_markdown = spoken_markdown.slice(0, firstDiag).trim();
  }

  if (code_blocks.length > 0 && spoken_markdown.includes("```")) {
    const withoutFences = spoken_markdown.replace(/```[\s\S]*?```/g, "").trim();
    if (withoutFences) {
      spoken_markdown = withoutFences;
    }
  }

  return {
    spoken_markdown,
    spoken_clean: stripMarkdownToPlain(spoken_markdown),
    technical_markdown,
    how_to_draw_markdown,
    diagrams: answerObj?.diagrams || [],
    code_blocks,
  };
}

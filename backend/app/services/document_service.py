"""Document pipeline: upload -> extract -> quality-check -> sectionize/chunk -> embed (with reuse) -> ready/failed.

Generic for any CV, job description, project notes or supporting document. Nothing here is specific
to one person or one file: no hard-coded names, passages, document IDs or contact values.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import Document, DocumentChunk
from app.rag.chunker import split_text
from app.services import openai_service

log = logging.getLogger(__name__)

DOC_TYPES: dict[str, str] = {
    "cv": "CV / Resume",
    "jd": "Job description",
    "project": "Project notes",
    "work": "My project / work sample",
    "supporting": "Supporting document",
}
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".markdown"}
SUPPORTED_HELP = (
    "Supported formats: PDF with selectable text, Word .docx, and plain text (.txt / .md). "
    "Legacy Word .doc, PowerPoint, images and spreadsheets are not supported — "
    "save the file as .docx or PDF and upload again."
)
OCR_AVAILABLE = False  # no OCR engine is installed on this deployment; scanned PDFs fail explicitly
MAX_PREVIEW_CHARS = 20_000


def normalize_doc_type(value: str | None) -> str:
    v = (value or "").strip().lower()
    aliases = {"resume": "cv", "job": "jd", "my_project": "work", "work_sample": "work", "job_description": "jd", "notes": "project",
               "project_notes": "project", "document": "supporting", "other": "supporting"}
    v = aliases.get(v, v)
    return v if v in DOC_TYPES else "supporting"


def safe_extension(filename: str) -> str | None:
    ext = Path(filename).suffix.lower()
    return ext if ext in ALLOWED_EXTENSIONS else None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_BACKEND_DIR = Path(__file__).resolve().parents[2]


def upload_root() -> Path:
    p = Path(get_settings().upload_dir)
    return p if p.is_absolute() else _BACKEND_DIR / p


def resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _BACKEND_DIR / p


def store_file(workspace_id: uuid.UUID, filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    folder = upload_root() / str(workspace_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}{ext}"  # never trust user-supplied names on disk
    path.write_bytes(data)
    return str(path)


def remove_file(path: str | None) -> None:
    if not path:
        return
    try:
        p = resolve_path(path)
        if p.is_file():
            p.unlink()
    except OSError:
        log.warning("Could not remove stored file %s", path)


# ---------------------------------------------------------------------------
# Contact-detail redaction (pattern based — no stored personal values)
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@\s?[A-Za-z0-9.-]+\.\s?[A-Za-z]{2,}")
# +CC / (CC) prefixes followed by 9-12 digits with optional separators
_PHONE_RE = re.compile(r"(?<!\w)(?:\(?\+?\d{1,3}\)?[\s.-]*)?(?:\d[\s.-]?){9,11}\d(?!\w)")
# A line that looks like a postal address: street-ish words plus a 5-6 digit postal code
_ADDRESS_LINE_RE = re.compile(
    r"^.*\b(flat|apartment|apt|house|street|st\.|road|rd\.|lane|nagar|society|sector|colony|avenue|ave\.)\b.*\b\d{5,6}\b.*$",
    re.I | re.M,
)


_ADDRESS_WORD_RE = re.compile(
    r"\b(flat|apartment|apt|house|street|st\.|road|rd\.|lane|nagar|society|sector|colony|avenue|ave\.|"
    r"building|bldg|floor|plot|block|city|suite|unit)\b|\bno\.\s*\d", re.I)
_POSTAL_RE = re.compile(r"(?<![\d-])(?!(?:19|20)\d{2}\b)\d{5,6}(?:-\d{4})?\b")


def _redact_address_blocks(text: str) -> str:
    """Postal addresses often wrap over several short lines: redact from the first line with an address word
    through the line holding the postal code, when they are within a few lines of each other."""
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if _ADDRESS_WORD_RE.search(lines[i]) and len(lines[i]) <= 90:
            for j in range(i, min(i + 7, len(lines))):
                if _POSTAL_RE.search(lines[j]) and all(len(x) <= 90 for x in lines[i:j + 1]):
                    lines[i:j + 1] = ["[Address redacted]"]
                    break
        i += 1
    return "\n".join(lines)


def redact_contact_pii(text: str) -> str:
    """Strip emails, phone numbers and postal addresses before text is stored for prompts."""
    if not text:
        return ""
    s = _redact_address_blocks(text)
    s = _ADDRESS_LINE_RE.sub("[Address redacted]", s)
    s = _EMAIL_RE.sub("[Email redacted]", s)
    s = _PHONE_RE.sub("[Phone redacted]", s)
    return s


# ---------------------------------------------------------------------------
# Extraction + quality gate
# ---------------------------------------------------------------------------
class ExtractionError(ValueError):
    """Raised when a file cannot yield trustworthy text. Message is shown to the user."""


@dataclass
class Extraction:
    pages: list[tuple[int | None, str]]
    method: str
    warnings: list[str] = field(default_factory=list)
    page_count: int = 0


def _extract_pdf(path: str) -> Extraction:
    from pypdf import PdfReader
    try:
        reader = PdfReader(path)
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError(f"The PDF could not be opened ({exc.__class__.__name__}). It may be corrupt or password protected.") from exc
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError("The PDF is password protected. Remove the password and upload again.") from exc
    pages: list[tuple[int | None, str]] = []
    empty_pages: list[int] = []
    column_pages: list[int] = []
    for i, page in enumerate(reader.pages, 1):
        try:
            plain = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            plain = ""
        txt, columns = plain, False
        try:
            ordered, columns = _positional_text(page)
            # Only trust the positional reading if it kept roughly all of the text.
            n_plain, n_pos = len(re.sub(r"\s", "", plain)), len(re.sub(r"\s", "", ordered))
            if n_pos and 0.8 * n_plain <= n_pos <= 1.25 * n_plain + 50:
                txt = ordered
            else:
                columns = False
        except Exception:  # noqa: BLE001
            columns = False
        if columns:
            column_pages.append(i)
        if len(re.sub(r"\s", "", txt)) < 20:
            empty_pages.append(i)
        pages.append((i, _clean_glyphs(txt)))
    method = "pypdf text layer, positional reading order"
    if column_pages:
        method += f" (multi-column layout detected on page(s) {', '.join(map(str, column_pages))})"
    ex = Extraction(pages=pages, method=method, page_count=len(reader.pages))
    if empty_pages and len(empty_pages) < len(pages):
        ex.warnings.append(
            f"No readable text on page(s) {', '.join(map(str, empty_pages))} — they may be scanned images "
            "and were skipped (OCR is not available)."
        )
    return ex


_BULLET_GLYPHS = re.compile("[▪●◦]")


def _clean_glyphs(text: str) -> str:
    return _BULLET_GLYPHS.sub("•", text)


def _mult(m: list[float], n: list[float]) -> list[float]:
    return [m[0] * n[0] + m[1] * n[2], m[0] * n[1] + m[1] * n[3], m[2] * n[0] + m[3] * n[2],
            m[2] * n[1] + m[3] * n[3], m[4] * n[0] + m[5] * n[2] + n[4], m[4] * n[1] + m[5] * n[3] + n[5]]


def _positional_text(page) -> tuple[str, bool]:
    """Rebuild reading order from glyph positions. Detects a vertical gutter (sidebar / two-column CVs) and
    reads each column top-to-bottom so headings stay with their bullets. Returns (text, columns_detected)."""
    frags: list[tuple[float, float, str, float]] = []

    def visit(text, cm, tm, _font, size):
        if not text or not text.strip():
            return
        m = _mult(list(tm), list(cm))
        eff = (abs(size) or 10.0) * (abs(m[0]) or 1.0)
        for k, line in enumerate(text.split("\n")):
            if line.strip():
                frags.append((m[4], m[5] - k * eff * 1.2, line.rstrip(), eff))

    page.extract_text(visitor_text=visit)
    width = float(page.mediabox.width)
    frags = [f for f in frags if not re.fullmatch(r"\s*(page\s*)?\d+\s*(of\s*\d+)?\s*", f[2], re.I)]
    if not frags:
        return "", False

    split = _find_gutter(frags, width)
    cols = [frags] if split is None else [[f for f in frags if f[0] < split], [f for f in frags if f[0] >= split]]
    out: list[str] = []
    for col in cols:
        col = sorted(col, key=lambda f: (-round(f[1], 0), f[0]))
        prev_y, prev_sz = None, None
        line_parts: list[str] = []
        for x, y, t, sz in col:
            if prev_y is not None and abs(prev_y - y) <= max(2.0, 0.35 * sz):
                line_parts.append(t)  # same visual line inside this column
                continue
            if line_parts:
                out.append(" ".join(line_parts))
            if prev_y is not None and prev_y - y > 1.7 * max(sz, prev_sz or sz):
                out.append("")  # paragraph gap
            line_parts, prev_y, prev_sz = [t], y, sz
        if line_parts:
            out.append(" ".join(line_parts))
        out.append("")
    return "\n".join(out).strip(), split is not None


def _find_gutter(frags: list[tuple[float, float, str, float]], width: float) -> float | None:
    """Return the x of a column gutter if text clearly forms a left and a right block, else None."""
    starts = sorted({round(f[0]) for f in frags})
    best: float | None = None
    for a, b in zip(starts, starts[1:]):
        gap = b - a
        if gap < 0.12 * width or not (0.25 * width <= b <= 0.8 * width):
            continue
        left = [f for f in frags if f[0] <= a]
        right = [f for f in frags if f[0] >= b]
        lc, rc = sum(len(f[2]) for f in left), sum(len(f[2]) for f in right)
        if min(lc, rc) < 0.08 * (lc + rc) or len(right) < 4 or len(left) < 4:
            continue
        # Left-column lines must end before the right block starts (proportional fonts average ~0.42em per char).
        crossing = sum(1 for f in left if f[0] + len(f[2]) * f[3] * 0.42 > b + 0.03 * width)
        if crossing <= max(1, 0.15 * len(left)):
            best = float(b)
            break
    return best


def _extract_docx(path: str) -> Extraction:
    try:
        from docx import Document as Docx
        d = Docx(path)
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError("The Word file could not be read. Only .docx is supported (not legacy .doc).") from exc
    lines: list[str] = []
    for p in d.paragraphs:
        t = p.text.strip()
        if not t:
            lines.append("")
            continue
        style = (p.style.name if p.style is not None else "") or ""
        lines.append(f"## {t}" if style.lower().startswith(("heading", "title")) else t)
    for table in d.tables:
        lines.append("")
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            lines.append(" | ".join(c for c in cells if c))
    return Extraction(pages=[(None, "\n".join(lines))], method="python-docx", page_count=1)


def _extract_text(path: str) -> Extraction:
    raw = Path(path).read_bytes()
    if b"\x00" in raw[:4096]:
        raise ExtractionError("This file looks binary, not plain text. " + SUPPORTED_HELP)
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return Extraction(pages=[(None, raw.decode(enc))], method=f"plain text ({enc})", page_count=1)
        except UnicodeDecodeError:
            continue
    raise ExtractionError("The text file encoding could not be decoded.")


def extract(path: str) -> Extraction:
    if not resolve_path(path).is_file():
        raise ExtractionError("The stored file is missing on the server. Use Replace to upload it again.")
    path = str(resolve_path(path))
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        ex = _extract_pdf(path)
    elif ext == ".docx":
        ex = _extract_docx(path)
    elif ext in {".txt", ".md", ".markdown"}:
        ex = _extract_text(path)
    else:
        raise ExtractionError(SUPPORTED_HELP)
    _quality_gate(ex, is_pdf=ext == ".pdf")
    return ex


def _quality_gate(ex: Extraction, is_pdf: bool) -> None:
    """Refuse to index empty or garbled text instead of silently storing junk."""
    full = "\n".join(t for _, t in ex.pages)
    visible = re.sub(r"\s", "", full)
    if len(visible) < 40:
        if is_pdf:
            raise ExtractionError(
                "No readable text could be extracted. This PDF appears to be scanned or image-only, and OCR "
                "is not available here. Upload a text-based PDF, a .docx, or paste the text into a .txt file."
            )
        raise ExtractionError("The document contains no readable text.")
    cid_hits = len(re.findall(r"\(cid:\d+\)", full))
    replacement = full.count("�")
    letters = sum(ch.isalpha() for ch in visible)
    if cid_hits > 20 or replacement > max(20, len(visible) * 0.02) or letters / max(1, len(visible)) < 0.45:
        raise ExtractionError(
            "Text extraction produced garbled characters (the PDF likely uses unmapped fonts). "
            "Export it again as PDF from Word/Google Docs, or upload a .docx/.txt version."
        )


# ---------------------------------------------------------------------------
# Sectionizing + chunking
# ---------------------------------------------------------------------------
_HEADING_MD_RE = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$")


_DATE_RANGE_RE = re.compile(
    r"((jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+)?(19|20)\d{2}\s*[-–—to]+\s*"
    r"(\d{1,2}\s+)?((jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+)?((19|20)\d{2}|present|current|now)",
    re.I,
)


def _heading_of(line: str) -> str | None:
    s = line.strip()
    if not s or len(s) > 70:
        return None
    m = _HEADING_MD_RE.match(s)
    if m:
        return m.group(1).strip()
    words = s.split()
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 4 and len(words) <= 7 and s.upper() == s and not re.search(r"[.,;]$", s):
        return s.title() if s.isupper() else s
    if s.endswith(":") and len(words) <= 6 and s[0].isupper():
        return s[:-1].strip()
    # Short Title Case line with no sentence punctuation/digits, e.g. "Key Responsibilities", "Good to Have"
    small = {"a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "with", "&", "/", "-"}
    if (2 <= len(words) <= 5 and len(letters) >= 6 and "," not in s and not re.search(r"[\d.,;:!?()•\-]$|^[•\-*]|\d", s)
            and words[0][0].isupper() and all(w[0].isupper() or w.lower() in small for w in words)):
        return s
    return None


def sectionize(pages: list[tuple[int | None, str]]) -> list[tuple[int | None, str | None, str]]:
    """Split page text into (page, section_title, text) blocks using generic heading heuristics."""
    blocks: list[tuple[int | None, str | None, str]] = []
    current_title: str | None = None
    for page, text in pages:
        buf: list[str] = []
        for line in text.splitlines():
            h = _heading_of(line)
            if h and _DATE_RANGE_RE.search(h):
                # A date-range line ("MARCH 2023 - FEBRUARY 2024") usually dates the role/heading written on
                # the line just above it — keep them together so every chunk says whose dates these are.
                while buf and not buf[-1].strip():
                    buf.pop()
                if buf and len(buf[-1].strip()) <= 120:
                    h = f"{buf.pop().strip()} · {h}"
                elif not buf and current_title and not _DATE_RANGE_RE.search(current_title):
                    h = f"{current_title} · {h}"  # role line was itself detected as a heading
            if h:
                if "".join(buf).strip():
                    blocks.append((page, current_title, "\n".join(buf).strip()))
                buf = []
                current_title = h[:200]
            else:
                buf.append(line)
        if "".join(buf).strip():
            blocks.append((page, current_title, "\n".join(buf).strip()))
    return blocks


def build_chunks(pages: list[tuple[int | None, str]]) -> list[dict]:
    s = get_settings()
    out: list[dict] = []
    for page, title, text in sectionize(pages):
        clean = redact_contact_pii(text)
        for piece in split_text(clean, s.chunk_size, s.chunk_overlap):
            if len(re.sub(r"\W", "", piece)) < 8:
                continue
            out.append({"page_number": page, "section_title": title, "content": piece})
    return out


# ---------------------------------------------------------------------------
# Ingestion (background task; owns its DB session)
# ---------------------------------------------------------------------------
def _meta(doc: Document) -> dict:
    try:
        return json.loads(doc.meta_json or "{}")
    except ValueError:
        return {}


async def ingest_document(document_id: uuid.UUID) -> None:
    s = get_settings()
    async with SessionLocal() as db:
        doc = await db.get(Document, document_id)
        if not doc:
            return
        try:
            doc.status, doc.error = "processing", None
            await db.commit()

            ex = extract(doc.storage_path)
            chunks = build_chunks(ex.pages)
            if not chunks:
                raise ExtractionError("No indexable text remained after extraction.")
            full_text = redact_contact_pii("\n\n".join(t for _, t in ex.pages if t.strip()))

            # Reuse embeddings for chunk text that is already embedded with the same model
            # (re-uploads, replacements and re-processing of unchanged content cost nothing).
            vectors: list[list[float] | None] = [None] * len(chunks)
            reused = new = 0
            if not s.llm_mock:
                contents = [c["content"] for c in chunks]
                rows = (await db.execute(
                    select(DocumentChunk.content, DocumentChunk.embedding)
                    .where(DocumentChunk.workspace_id == doc.workspace_id,
                           DocumentChunk.embedding_model == s.openai_embedding_model,
                           DocumentChunk.embedding.is_not(None),
                           DocumentChunk.content.in_(contents))
                )).all()
                known = {c: list(v) for c, v in rows}
                missing = [i for i, c in enumerate(contents) if c not in known]
                for i, c in enumerate(contents):
                    if c in known:
                        vectors[i] = known[c]
                        reused += 1
                if missing:
                    fresh = await openai_service.embed_texts([contents[i] for i in missing])
                    for i, v in zip(missing, fresh):
                        vectors[i] = v
                    new = len(missing)

            await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
            emb_model = None if s.llm_mock else s.openai_embedding_model
            db.add_all(
                DocumentChunk(document_id=doc.id, workspace_id=doc.workspace_id, chunk_index=i,
                              page_number=c["page_number"], section_title=c["section_title"],
                              content=c["content"], embedding_model=emb_model, embedding=vec)
                for i, (c, vec) in enumerate(zip(chunks, vectors))
            )
            sections = sorted({c["section_title"] for c in chunks if c["section_title"]})
            doc.extracted_text = full_text[:200_000]
            doc.embedding_model = emb_model
            doc.embedding_status = "keyword_only" if s.llm_mock else "ready"
            doc.meta_json = json.dumps({
                **_meta(doc),
                "method": ex.method,
                "page_count": ex.page_count,
                "char_count": len(full_text),
                "chunk_count": len(chunks),
                "section_count": len(sections),
                "sections": sections[:40],
                "warnings": ex.warnings,
                "embeddings_reused": reused,
                "embeddings_new": new,
            })
            doc.status, doc.error = "ready", None
            await db.commit()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user via document.error
            log.exception("Document ingestion failed: %s", document_id)
            await db.rollback()
            doc = await db.get(Document, document_id)
            if doc:
                await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
                doc.status = "failed"
                doc.error = str(exc)[:600] if isinstance(exc, ExtractionError) else (
                    f"Processing failed: {exc.__class__.__name__}: {str(exc)[:300]}")
                doc.extracted_text = ""
                await db.commit()


async def resume_interrupted_ingestion() -> None:
    async with SessionLocal() as db:
        ids = (await db.scalars(select(Document.id).where(Document.status.in_(("pending", "processing"))))).all()
    for doc_id in ids:
        await ingest_document(doc_id)


def document_payload(doc: Document, chunk_count: int | None = None, include_text: bool = False) -> dict:
    meta = _meta(doc)
    out = {
        "id": str(doc.id),
        "filename": doc.filename,
        "doc_type": normalize_doc_type(doc.doc_kind),
        "doc_type_label": DOC_TYPES[normalize_doc_type(doc.doc_kind)],
        "content_type": doc.content_type,
        "size_bytes": doc.size_bytes,
        "file_sha256": doc.file_sha256,
        "status": doc.status,
        "error": doc.error,
        "embedding_status": doc.embedding_status,
        "embedding_model": doc.embedding_model,
        "chunk_count": chunk_count if chunk_count is not None else meta.get("chunk_count", 0),
        "page_count": meta.get("page_count"),
        "char_count": meta.get("char_count"),
        "sections": meta.get("sections", []),
        "warnings": meta.get("warnings", []),
        "embeddings_reused": meta.get("embeddings_reused"),
        "embeddings_new": meta.get("embeddings_new"),
        "extraction_method": meta.get("method"),
        "preview": (doc.extracted_text or "")[:600],
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }
    if include_text:
        out["text"] = (doc.extracted_text or "")[:MAX_PREVIEW_CHARS]
        out["text_truncated"] = len(doc.extracted_text or "") > MAX_PREVIEW_CHARS
    return out


# ---------------------------------------------------------------------------
# Transcription vocabulary (generic: technical-looking terms found in the user's own documents)
# ---------------------------------------------------------------------------
_TERM_RE = re.compile(r"\b(?:[A-Za-z]+[A-Z][A-Za-z0-9]*|[A-Z]{2,6}\d?|[A-Za-z]+\d+[A-Za-z0-9]*)\b")
# Capitalised word in the middle of a sentence (after a lowercase word or a comma) — typical for tool names.
_MID_CAP_RE = re.compile(r"(?:(?<=[a-z] )|(?<=, ))([A-Z][a-z]{2,}[A-Za-z]*)\b")
_TERM_STOP = {"The", "This", "That", "And", "For", "With", "From", "Page", "Built", "Developed", "Designed",
              "Led", "Worked", "Owned", "Applied", "Supported", "Coordinated", "Converted", "Collaborated",
              "Remained", "Contributed", "Engineered", "Implemented", "Automated", "Evaluated", "Packaged",
              "Current Project", "Key Responsibilities", "Required Skills", "About", "Good"}


def vocabulary_terms(texts: list[str], limit: int = 60) -> list[str]:
    """Rank distinctive technical terms (PySpark, MWAA, S3, Delta Lake…) by frequency across documents."""
    from collections import Counter
    counts: Counter[str] = Counter()
    for t in texts:
        for m in _TERM_RE.findall(t or "") + _MID_CAP_RE.findall(t or ""):
            term = m.strip()
            if term in _TERM_STOP or len(term) < 2 or "redacted" in term.lower():
                continue
            counts[term] += 1
    return [t for t, _ in counts.most_common(limit)]

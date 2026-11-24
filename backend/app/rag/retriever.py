"""Retrieval for RAG.

- Document retrieval is restricted to the asker's own ready documents and, when the meeting has a
  document selection, to the selected documents only.
- pgvector cosine search when chunks are embedded, keyword scoring for chunks that are not.
"""
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import AIAnswer, Document, DocumentChunk, Meeting, TranscriptSegment
from app.services import openai_service
from app.services.diagram_renderer import parse_structured_answer


@dataclass
class DocHit:
    chunk_id: UUID
    document_id: UUID
    filename: str
    doc_type: str
    page_number: int | None
    section_title: str | None
    content: str
    score: float


@dataclass
class FullDoc:
    document_id: UUID
    filename: str
    doc_type: str
    text: str
    first_chunk_id: UUID | None


@dataclass
class TranscriptHit:
    segment_id: UUID
    offset_ms: int
    text: str


@dataclass
class PriorQA:
    answer_id: UUID
    question_text: str
    answer_text: str
    spoken_clean: str
    design_state: dict[str, Any] | None


def fmt_ts(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# Document scope
# ---------------------------------------------------------------------------
def meeting_document_ids(meeting: Meeting | None) -> list[UUID] | None:
    if meeting is None or meeting.document_ids_json is None:
        return None
    try:
        return [UUID(str(x)) for x in json.loads(meeting.document_ids_json)]
    except (ValueError, TypeError):
        return None


def _doc_scope(user_id: UUID, doc_ids: list[UUID] | None):
    cond = [Document.uploaded_by == user_id, Document.status == "ready"]
    if doc_ids is not None:
        cond.append(Document.id.in_(doc_ids or [UUID(int=0)]))
    return and_(*cond)


_STOP = {"what", "the", "and", "for", "with", "how", "you", "your", "have", "that", "this", "from", "into",
         "were", "was", "are", "some", "any", "did", "does", "which", "who", "when", "where", "why", "about",
         "tell", "explain", "can", "could", "would", "should", "there", "their", "them", "they", "been", "let",
         "know", "please", "also", "used", "use", "its", "our", "all", "one", "out", "like"}


def _keyword_score(query: str, title: str | None, content: str) -> float:
    toks = {t for t in re.findall(r"[a-z0-9][a-z0-9_+#.-]{1,}", query.lower()) if t not in _STOP}
    if not toks:
        return 0.0
    body, head = content.lower(), (title or "").lower()
    hits = sum((2.0 if t in head else 0.0) + (1.0 if t in body else 0.0) for t in toks)
    return round(min(0.99, hits / (len(toks) * 1.5)), 4)


async def search_documents(db: AsyncSession, user_id: UUID | None, query: str,
                           doc_ids: list[UUID] | None = None, top_k: int | None = None,
                           query_vec: list[float] | None = None) -> list[DocHit]:
    if user_id is None:
        return []
    top_k = top_k or get_settings().rag_top_k
    scope = _doc_scope(user_id, doc_ids)
    base = (select(DocumentChunk, Document.filename, Document.doc_kind)
            .join(Document, Document.id == DocumentChunk.document_id).where(scope))

    hits: dict[UUID, DocHit] = {}
    s = get_settings()
    has_vectors = await db.scalar(select(func.count(DocumentChunk.id))
                                  .join(Document, Document.id == DocumentChunk.document_id)
                                  .where(scope, DocumentChunk.embedding.is_not(None)))
    if has_vectors and not s.llm_mock:
        vec = query_vec if query_vec is not None else (await openai_service.embed_texts([query]))[0]
        dist = DocumentChunk.embedding.cosine_distance(vec)
        rows = (await db.execute(base.add_columns(dist.label("d"))
                                 .where(DocumentChunk.embedding.is_not(None)).order_by(dist).limit(top_k))).all()
        for c, fn, kind, d in rows:
            hits[c.id] = DocHit(c.id, c.document_id, fn, kind, c.page_number, c.section_title, c.content,
                                round(1.0 - float(d), 4))

    # Keyword pass: covers un-embedded chunks and boosts exact-term matches the vector search may rank low.
    rows = (await db.execute(base)).all()
    for c, fn, kind in rows:
        sc = _keyword_score(query, c.section_title, c.content)
        if sc >= 0.34 and (c.id not in hits or hits[c.id].score < sc):
            hits[c.id] = DocHit(c.id, c.document_id, fn, kind, c.page_number, c.section_title, c.content, sc)
    return sorted(hits.values(), key=lambda h: h.score, reverse=True)[:top_k]


async def full_documents(db: AsyncSession, user_id: UUID | None, doc_ids: list[UUID] | None,
                         kinds: tuple[str, ...], max_chars: int) -> list[FullDoc]:
    """Whole (redacted) text of small core documents such as the CV and JD, in chunk order."""
    if user_id is None:
        return []
    docs = (await db.scalars(select(Document).where(_doc_scope(user_id, doc_ids), Document.doc_kind.in_(kinds))
                             .order_by(Document.created_at.desc()))).all()
    # Core documents first (CV, JD, client notes), then large work samples get whatever budget remains.
    priority = {"cv": 0, "jd": 1, "project": 2, "work": 3, "supporting": 4}
    docs = sorted(docs, key=lambda d: priority.get(d.doc_kind, 9))
    out: list[FullDoc] = []
    budget = max_chars
    for d in docs:
        chunks = (await db.execute(select(DocumentChunk.id, DocumentChunk.section_title, DocumentChunk.content)
                                   .where(DocumentChunk.document_id == d.id)
                                   .order_by(DocumentChunk.chunk_index))).all()
        if not chunks:
            continue
        parts, last_title = [], None
        for _, title, content in chunks:
            if title and title != last_title:
                parts.append(f"[{title}]")
                last_title = title
            parts.append(content)
        text = "\n".join(parts)
        if len(text) > budget:
            text = text[:budget] + "\n[…truncated]"
        budget -= len(text)
        out.append(FullDoc(d.id, d.filename, d.doc_kind, text, chunks[0][0]))
        if budget <= 500:
            break
    return out


# ---------------------------------------------------------------------------
# Meeting context
# ---------------------------------------------------------------------------
async def recent_transcript(db: AsyncSession, meeting_id: UUID, max_chars: int | None = None) -> list[TranscriptHit]:
    """The whole session transcript (what was said by everyone); the newest part is kept if it exceeds the cap."""
    cap = max_chars or get_settings().rag_transcript_max_chars
    rows = (await db.scalars(select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id)
                             .order_by(TranscriptSegment.start_offset_ms.desc()))).all()
    out: list[TranscriptHit] = []
    used = 0
    for r in rows:  # newest first until the cap, then restore chronological order
        used += len(r.text) + 12
        if used > cap and out:
            break
        out.append(TranscriptHit(r.id, r.start_offset_ms, r.text))
    out.reverse()
    return out


async def recent_answers(db: AsyncSession, meeting_id: UUID, exclude_answer_id: UUID | None = None,
                         limit: int = 20) -> list[PriorQA]:
    stmt = (select(AIAnswer).where(AIAnswer.meeting_id == meeting_id, AIAnswer.status == "completed")
            .order_by(AIAnswer.created_at.desc()).limit(limit))
    if exclude_answer_id is not None:
        # Only turns that came BEFORE the current question belong to its history.
        current = await db.scalar(select(AIAnswer.created_at).where(AIAnswer.id == exclude_answer_id))
        stmt = stmt.where(AIAnswer.id != exclude_answer_id)
        if current is not None:
            stmt = stmt.where(AIAnswer.created_at < current)
    rows = list((await db.scalars(stmt)).all())
    rows.reverse()
    out: list[PriorQA] = []
    for r in rows:
        parsed = parse_structured_answer(r.answer_text or "", is_complete=True)
        out.append(PriorQA(r.id, r.question_text, r.answer_text or "", parsed.spoken_clean, parsed.design_state))
    return out

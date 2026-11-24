"""RAG orchestration: intent -> scoped document evidence -> meeting context -> prompt -> sources."""
import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Document, Meeting
from app.services import openai_service
from app.rag import retriever
from app.rag.prompt import DOC_TYPE_LABEL, build_messages
from app.rag.retriever import DocHit, FullDoc, PriorQA, TranscriptHit

FULL_DOC_CHAR_BUDGET = 60_000  # ~15k tokens of whole documents, identical every turn so the prompt cache serves it


@dataclass
class RagContext:
    messages: list[dict[str, str]]
    intent: str
    full_docs: list[FullDoc] = field(default_factory=list)
    docs: list[DocHit] = field(default_factory=list)
    transcript: list[TranscriptHit] = field(default_factory=list)
    prior_qas: list[PriorQA] = field(default_factory=list)

    def source_payloads(self) -> list[dict]:
        out = [{
            "source_type": "document", "chunk_id": f.first_chunk_id, "segment_id": None,
            "label": f"{f.filename} ({DOC_TYPE_LABEL.get(f.doc_type, 'Document')}, full text)",
            "offset_ms": None, "score": None, "snippet": f.text[:400],
        } for f in self.full_docs]
        seen = {f.document_id for f in self.full_docs}
        out += [{
            "source_type": "document", "chunk_id": d.chunk_id, "segment_id": None,
            "label": d.filename + (f" · {d.section_title}" if d.section_title else "")
                     + (f" · p.{d.page_number}" if d.page_number else ""),
            "offset_ms": None, "score": round(d.score, 4), "snippet": d.content[:400],
        } for d in self.docs if d.document_id not in seen]
        return out


async def build_context(db: AsyncSession, meeting: Meeting, question: str, selected_text: str | None,
                        user_id: UUID | None = None, exclude_answer_id: UUID | None = None) -> RagContext:
    intent = "chat"  # single generic flow: documents are always provided, the model decides what is relevant
    doc_ids = retriever.meeting_document_ids(meeting)
    query = f"{question}\n{selected_text}" if selected_text else question

    full_docs: list[FullDoc] = []
    passages: list[DocHit] = []
    has_any = False
    # Start the query embedding (network) now so it runs while the database reads below are happening.
    embed_task = None
    if user_id is not None and not get_settings().llm_mock:
        embed_task = asyncio.create_task(openai_service.embed_texts([query]))
    if user_id is not None:
        scope = [Document.uploaded_by == user_id, Document.status == "ready"]
        if doc_ids is not None:
            scope.append(Document.id.in_(doc_ids or [UUID(int=0)]))
        has_any = bool(await db.scalar(select(func.count(Document.id)).where(*scope)))
        if has_any:
            kinds = ("cv", "jd", "project", "work", "supporting")
            full_docs = await retriever.full_documents(db, user_id, doc_ids, kinds, FULL_DOC_CHAR_BUDGET)
            query_vec = None
            if embed_task is not None:
                try:
                    query_vec = (await embed_task)[0]
                except Exception:  # noqa: BLE001 - fall back to the normal path inside search_documents
                    query_vec = None
            passages = await retriever.search_documents(db, user_id, query, doc_ids, query_vec=query_vec)
    if embed_task is not None:  # never leave an un-retrieved task error behind
        if not embed_task.done():
            embed_task.cancel()
        elif not embed_task.cancelled():
            embed_task.exception()

    transcript = await retriever.recent_transcript(db, meeting.id)
    prior_qas = await retriever.recent_answers(db, meeting.id, exclude_answer_id=exclude_answer_id)
    messages = build_messages(question, selected_text, intent, full_docs=full_docs, passages=passages,
                              transcript=transcript, prior_qas=prior_qas, has_any_documents=has_any)
    return RagContext(messages, intent, full_docs, passages, transcript, prior_qas)

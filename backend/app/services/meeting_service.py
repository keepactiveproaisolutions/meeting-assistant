"""Meeting lifecycle, transcript ingestion and snapshots."""
import re
import secrets
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import (AIAnswer, DetectedQuestion, Meeting, TranscriptSegment)
from app.realtime.events import Event
from app.realtime.manager import manager
from app.schemas.schemas import AnswerOut, MeetingOut, MeetingSnapshot, QuestionOut, SegmentOut, UtteranceOut
from app.services import openai_service
from app.services.question_detector import detect_questions
from app.services.transcript_assembler import assemble_utterances

# Unambiguous alphabet (no 0/O/1/I) for codes people read aloud or type.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def meeting_out(m: Meeting) -> MeetingOut:
    out = MeetingOut.model_validate(m)
    s = get_settings()
    base_url = s.web_app_url.rstrip("/")
    if s.allow_lan_dev and ("localhost" in base_url or "127.0.0.1" in base_url):
        from app.api.auth import _detect_lan_ip
        lan_ip = _detect_lan_ip()
        if lan_ip:
            base_url = base_url.replace("localhost", lan_ip).replace("127.0.0.1", lan_ip)
    out.join_url = f"{base_url}/join?code={m.code}"
    return out


async def create_meeting(db: AsyncSession, workspace_id: UUID, user_id: UUID, title: str) -> Meeting:
    for _ in range(5):
        code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
        meeting = Meeting(workspace_id=workspace_id, created_by=user_id, title=title, code=code)
        db.add(meeting)
        try:
            await db.commit()
            return meeting
        except IntegrityError:
            await db.rollback()
    raise RuntimeError("Could not allocate a unique meeting code")


_REPEAT_RE = re.compile(r"\b((?:\w+[\s,.;:!?-]+){0,2}?\w+)(?:[\s,.;:!?-]+\1\b){2,}", re.I)


def clean_transcript_text(text: str) -> str:
    """Collapse speech-to-text repetition loops ("security, security, security, …") — a known Whisper
    hallucination on short clips. Only 3+ immediate repeats of the same 1-3 word phrase are collapsed."""
    prev = None
    while prev != text:
        prev, text = text, _REPEAT_RE.sub(r"\1", text)
    return text.strip()


_VOCAB_CACHE: dict[UUID, tuple[float, list[str]]] = {}


async def _meeting_vocabulary(db: AsyncSession, meeting: Meeting) -> list[str]:
    """Technical terms from the documents this meeting uses (owner's selection), cached for 60 s."""
    import time
    from app.models import Document, DocumentChunk
    from app.rag.retriever import meeting_document_ids
    from app.services.document_service import vocabulary_terms
    hit = _VOCAB_CACHE.get(meeting.id)
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    q = (select(DocumentChunk.content).join(Document, Document.id == DocumentChunk.document_id)
         .where(Document.uploaded_by == meeting.created_by, Document.status == "ready"))
    ids = meeting_document_ids(meeting)
    if ids is not None:
        q = q.where(Document.id.in_(ids or [UUID(int=0)]))
    terms = vocabulary_terms(list((await db.scalars(q)).all()), 60)
    _VOCAB_CACHE[meeting.id] = (time.monotonic(), terms)
    return terms


async def ingest_audio_chunk(db: AsyncSession, meeting: Meeting, audio: bytes,
                             chunk_index: int, offset_ms: int, duration_ms: int) -> SegmentOut | None:
    """Transcribe one chunk, persist raw segment, assemble continuous utterances, detect genuine questions."""
    import logging
    import time as _t
    _timing = {"t0": _t.perf_counter()}
    _log = logging.getLogger("timing.ingest")
    prev = await db.scalar(select(TranscriptSegment.text).where(TranscriptSegment.meeting_id == meeting.id)
                           .order_by(TranscriptSegment.start_offset_ms.desc()).limit(1))
    vocab = await _meeting_vocabulary(db, meeting)
    _timing["pre"] = _t.perf_counter()
    text = await openai_service.transcribe_wav(audio, context=prev or "", vocabulary=vocab)
    _timing["stt"] = _t.perf_counter()
    return await persist_transcript_text(db, meeting, text or "", chunk_index, offset_ms, duration_ms, _timing)


async def persist_transcript_text(db: AsyncSession, meeting: Meeting, text: str, chunk_index: int, offset_ms: int,
                                  duration_ms: int, _timing: dict | None = None) -> SegmentOut | None:
    """Persist one finalized transcript piece (Standard chunk or Live turn) as a raw segment, re-assemble paragraphs,
    detect questions and broadcast to every client of the meeting."""
    import logging
    import time as _t
    _log = logging.getLogger("timing.ingest")
    if _timing is None:
        now = _t.perf_counter()
        _timing = {"t0": now, "pre": now, "stt": now}
    text = clean_transcript_text(text or "")
    if not text or len(text.strip(" .")) < 2:
        return None

    # Ensure monotonically increasing chunk_index and offset_ms when a desktop client reconnects/restarts on an active meeting
    max_row = (
        await db.execute(
            select(
                func.max(TranscriptSegment.chunk_index),
                func.max(TranscriptSegment.end_offset_ms),
            ).where(TranscriptSegment.meeting_id == meeting.id)
        )
    ).one_or_none()
    if max_row is not None:
        max_idx, max_end_ms = max_row[0], max_row[1]
        if max_idx is not None and chunk_index <= max_idx:
            chunk_index = int(max_idx) + 1
        if max_end_ms is not None and offset_ms < int(max_end_ms):
            offset_ms = int(max_end_ms)

    segment = TranscriptSegment(meeting_id=meeting.id, chunk_index=chunk_index,
                                start_offset_ms=offset_ms, end_offset_ms=offset_ms + duration_ms, text=text)
    db.add(segment)
    try:
        await db.flush()
    except IntegrityError:  # duplicate upload retry
        await db.rollback()
        return None

    # Fetch recent segments to assemble continuous conversational utterance paragraphs
    segs = (await db.scalars(select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting.id)
                             .order_by(TranscriptSegment.start_offset_ms))).all()
    utterances = assemble_utterances(segs)
    _timing["asm"] = _t.perf_counter()
    latest_utt = utterances[-1] if utterances else None

    # Detect questions on assembled utterance with full context to eliminate false fragments
    q_rows: list[DetectedQuestion] = []
    if latest_utt and latest_utt.questions:
        for q_text in latest_utt.questions:
            # Check if this question was already recorded for this meeting
            exists = await db.scalar(
                select(DetectedQuestion.id).where(
                    DetectedQuestion.meeting_id == meeting.id,
                    DetectedQuestion.text == q_text,
                )
            )
            if not exists:
                # A question still being spoken grows chunk by chunk: extend the earlier partial instead of
                # storing every prefix as a separate question.
                recent = (await db.scalars(select(DetectedQuestion).where(DetectedQuestion.meeting_id == meeting.id)
                                           .order_by(DetectedQuestion.created_at.desc()).limit(5))).all()
                partial = next((r for r in recent if q_text.startswith(r.text.rstrip("?").rstrip())), None)
                if partial is not None:
                    partial.text = q_text
                    q_rows.append(partial)
                    continue
                q_row = DetectedQuestion(
                    meeting_id=meeting.id,
                    segment_id=segment.id,
                    text=q_text,
                    confidence=0.9,
                )
                q_rows.append(q_row)
                db.add(q_row)

    segment.is_question = bool(q_rows or (latest_utt and latest_utt.is_question))
    await db.commit()
    _timing["commit"] = _t.perf_counter()

    # Broadcast assembled utterance (for continuous Wispr Flow paragraph view)
    if utterances:
        for utt in utterances[-2:]:
            await manager.broadcast(meeting.id, Event.TRANSCRIPT_UTTERANCE, utt.model_dump(mode="json"))

    # Broadcast raw segment (for traceability / raw view)
    seg_out = SegmentOut.model_validate(segment)
    await manager.broadcast(meeting.id, Event.TRANSCRIPT_SEGMENT, seg_out.model_dump(mode="json"))

    for q in q_rows:
        await manager.broadcast(meeting.id, Event.QUESTION_DETECTED, QuestionOut.model_validate(q).model_dump(mode="json"))

    _timing["sent"] = _t.perf_counter()
    ms = lambda a, b: int((_timing[b] - _timing[a]) * 1000)  # noqa: E731
    _log.info("chunk %s: prep %d ms | speech-to-text %d ms | db+assemble %d ms (%d segments) | commit %d ms | "
              "broadcast %d ms | total %d ms", chunk_index, ms('t0', 'pre'), ms('pre', 'stt'), ms('stt', 'asm'),
              len(segs), ms('asm', 'commit'), ms('commit', 'sent'), ms('t0', 'sent'))
    return seg_out


def _with_partial(a: AnswerOut) -> AnswerOut:
    """Answers still streaming have no stored text yet: include what has streamed so far."""
    if a.status == "streaming":
        from app.services.answer_service import partial_text
        a.answer_text = partial_text.get(str(a.id), a.answer_text or "")
    return a


async def snapshot(db: AsyncSession, meeting: Meeting) -> MeetingSnapshot:
    segs = (await db.scalars(select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting.id)
                             .order_by(TranscriptSegment.start_offset_ms))).all()
    utterances = assemble_utterances(segs)
    qs = (await db.scalars(select(DetectedQuestion).where(DetectedQuestion.meeting_id == meeting.id)
                           .order_by(DetectedQuestion.created_at))).all()
    ans = (await db.scalars(select(AIAnswer).where(AIAnswer.meeting_id == meeting.id)
                            .order_by(AIAnswer.created_at))).all()
    return MeetingSnapshot(
        meeting=meeting_out(meeting),
        segments=[SegmentOut.model_validate(x) for x in segs],
        utterances=utterances,
        questions=[QuestionOut.model_validate(x) for x in qs],
        answers=[_with_partial(AnswerOut.model_validate(x)) for x in ans],
        partials=__import__("app.services.live_transcription", fromlist=["x"]).partials(meeting.id),
        live=__import__("app.services.live_transcription", fromlist=["x"]).active(meeting.id),
    )

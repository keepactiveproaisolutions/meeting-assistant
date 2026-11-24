"""Answer generation: RAG context + streamed OpenAI completion broadcast to the meeting room."""
import asyncio
import logging
import time
from datetime import datetime, timezone
from uuid import UUID

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import AIAnswer, AnswerSource, Meeting
from app.rag.pipeline import build_context
from app.realtime.events import Event
from app.realtime.manager import manager
from app.screen_intelligence import service as screen_service
from app.services import openai_service
from app.services.diagram_renderer import parse_structured_answer

log = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()  # keep strong refs to fire-and-forget tasks
# Text streamed so far for answers still being generated. A client that (re)connects mid-stream gets this in its
# snapshot; every delta carries its character offset so clients can skip text they already have (no duplicates).
partial_text: dict[str, str] = {}


async def create_answer(db, meeting: Meeting, user_id: UUID, question: str,
                        selected_text: str | None, question_id: UUID | None) -> AIAnswer:
    answer = AIAnswer(meeting_id=meeting.id, asked_by=user_id, question_id=question_id,
                      question_text=question, context_text=selected_text,
                      model=get_settings().openai_chat_model)
    db.add(answer)
    await db.commit()
    return answer


ANSWER_DEADLINE_S = 150  # hard ceiling per answer; normal answers finish in 5-20 s


async def _guarded(answer_id: UUID) -> None:
    """Run an answer but never let it hang: after the deadline it is marked failed and clients are told."""
    try:
        await asyncio.wait_for(run_answer(answer_id), timeout=ANSWER_DEADLINE_S)
    except asyncio.TimeoutError:
        log.error("Answer timed out after %s s: %s", ANSWER_DEADLINE_S, answer_id)
        async with SessionLocal() as db:
            answer = await db.get(AIAnswer, answer_id)
            if answer is not None and answer.status != "completed":
                answer.status = "failed"
                partial_text.pop(str(answer_id), None)
                await db.commit()
                await manager.broadcast(answer.meeting_id, Event.ANSWER_FAILED,
                                        {"answer_id": str(answer_id), "message": "The answer took too long. Please send it again."})


def schedule_answer(answer_id: UUID) -> None:
    task = asyncio.create_task(_guarded(answer_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def run_answer(answer_id: UUID) -> None:
    t_start = time.monotonic()
    first_word_ms: int | None = None
    async with SessionLocal() as db:
        answer = await db.get(AIAnswer, answer_id)
        meeting = await db.get(Meeting, answer.meeting_id)
        mid = meeting.id
        await manager.broadcast(mid, Event.ANSWER_STARTED, {
            "answer_id": str(answer.id), "question_id": str(answer.question_id) if answer.question_id else None,
            "question_text": answer.question_text, "context_text": answer.context_text,
        })
        try:
            ctx = await build_context(db, meeting, answer.question_text, answer.context_text,
                                      # One session for every device and person in the meeting: answers always use the
                                      # meeting OWNER's documents, whoever (phone, web, desktop) asked the question.
                                      user_id=meeting.created_by, exclude_answer_id=answer.id)
            # Only track actual uploaded documents as sources; do not clutter with raw audio transcript chunks
            doc_sources = [p for p in ctx.source_payloads() if p.get("source_type") == "document"]
            rows = [AnswerSource(answer_id=answer.id, **p) for p in doc_sources]
            if rows:
                db.add_all(rows)
                await db.commit()
            await manager.broadcast(mid, Event.ANSWER_SOURCES, {
                "answer_id": str(answer.id),
                "intent": ctx.intent,
                "sources": [{"id": str(r.id), "source_type": r.source_type, "label": r.label,
                             "snippet": r.snippet, "offset_ms": r.offset_ms, "score": r.score} for r in rows],
            })

            parts: list[str] = []
            emitted_diagram_count = 0
            messages = ctx.messages
            screenshot = screen_service.take_image(answer.id)  # only present for Screen Intelligence questions
            if screenshot:
                messages = screen_service.attach_image(messages, screenshot)
            async for delta in openai_service.stream_chat(messages):  # bounded by the client timeouts
                if first_word_ms is None:
                    first_word_ms = int((time.monotonic() - t_start) * 1000)
                offset = sum(len(x) for x in parts)
                parts.append(delta)
                partial_text[str(answer.id)] = "".join(parts)
                await manager.broadcast(mid, Event.ANSWER_DELTA,
                                        {"answer_id": str(answer.id), "delta": delta, "offset": offset})
                # Check if a new fenced mermaid block just closed during streaming
                current_text = "".join(parts)
                if "```" in delta or "```mermaid" in current_text.lower():
                    partial_parsed = parse_structured_answer(current_text, is_complete=False)
                    while emitted_diagram_count < len(partial_parsed.diagrams):
                        diag = partial_parsed.diagrams[emitted_diagram_count]
                        emitted_diagram_count += 1
                        await manager.broadcast(
                            mid,
                            Event.ANSWER_DIAGRAM,
                            {"answer_id": str(answer.id), "diagram": diag.to_dict()},
                        )

            answer.answer_text = "".join(parts)
            answer.status = "completed"
            partial_text.pop(str(answer.id), None)
            answer.completed_at = datetime.now(timezone.utc)
            await db.commit()

            final_parsed = parse_structured_answer(answer.answer_text, is_complete=True)
            while emitted_diagram_count < len(final_parsed.diagrams):
                diag = final_parsed.diagrams[emitted_diagram_count]
                emitted_diagram_count += 1
                await manager.broadcast(
                    mid,
                    Event.ANSWER_DIAGRAM,
                    {"answer_id": str(answer.id), "diagram": diag.to_dict()},
                )

            await manager.broadcast(mid, Event.ANSWER_COMPLETED, {
                "answer_id": str(answer.id),
                "answer_text": answer.answer_text,
                "spoken_markdown": final_parsed.spoken_markdown,
                "spoken_clean": final_parsed.spoken_clean,
                "technical_markdown": final_parsed.technical_markdown,
                "how_to_draw_markdown": final_parsed.how_to_draw_markdown,
                "diagrams": [d.to_dict() for d in final_parsed.diagrams],
                "code_blocks": final_parsed.code_blocks,
                "design_state": final_parsed.design_state,
                "first_word_ms": first_word_ms,
                "total_ms": int((time.monotonic() - t_start) * 1000),
            })
        except Exception:  # noqa: BLE001
            log.exception("Answer generation failed: %s", answer_id)
            await db.rollback()
            answer = await db.get(AIAnswer, answer_id)
            answer.status = "failed"
            partial_text.pop(str(answer_id), None)
            await db.commit()
            await manager.broadcast(mid, Event.ANSWER_FAILED,
                                    {"answer_id": str(answer_id), "message": "Could not generate an answer."})


# --- Prompt-cache warming (OpenAI/Anthropic latency guides: pre-warm the shared prefix so the first real question
# --- does not pay the cache-miss penalty; caches expire after a few minutes of silence).
_warmers: dict[UUID, asyncio.Task] = {}
WARM_INTERVAL_S = 240
_WARM_QUESTIONS = ("Tell me about your experience with this kind of project.",
                   "How would you design the data pipeline for this migration?")


async def _warm_once(meeting_id: UUID, user_id: UUID) -> None:
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None:
            return
        for q in _WARM_QUESTIONS:
            ctx = await build_context(db, meeting, q, None, user_id=user_id)
            await openai_service.warm_prefix(ctx.messages)


async def _warm_loop(meeting_id: UUID, user_id: UUID) -> None:
    try:
        while manager.count(meeting_id) > 0:
            try:
                await _warm_once(meeting_id, user_id)
            except Exception:  # noqa: BLE001 - warming is best effort, never affects answers
                log.debug("cache warm failed", exc_info=True)
            await asyncio.sleep(WARM_INTERVAL_S)
    finally:
        _warmers.pop(meeting_id, None)


def ensure_warmer(meeting_id: UUID, user_id: UUID) -> None:
    """Start (once per meeting) a background loop that keeps the prompt cache warm while clients are connected."""
    if get_settings().llm_mock or meeting_id in _warmers:
        return
    _warmers[meeting_id] = asyncio.create_task(_warm_loop(meeting_id, user_id))

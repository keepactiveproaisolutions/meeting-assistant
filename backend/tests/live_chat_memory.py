"""Live chat-memory check (real model, costs a few cents; not part of the zero-cost suite).
Five scenarios: decision memory, pronoun follow-up, mid-session correction, transcript decision, CV/JD on follow-ups.
Run:  .venv/Scripts/python.exe tests\live_chat_memory.py"""
import asyncio, re, sys, time
from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func
from app.core.database import SessionLocal
from app.core.dev_auth import ensure_dev_identity
from app.models import AIAnswer, Meeting, TranscriptSegment, Document
from app.rag.pipeline import build_context
from app.services import meeting_service, openai_service
from app.services.diagram_renderer import parse_structured_answer


async def ask(db, meeting, user, q):
    ctx = await build_context(db, meeting, q, None, user_id=user.id)
    parts = []
    async for d in openai_service.stream_chat(ctx.messages):
        parts.append(d)
    full = "".join(parts)
    spoken = parse_structured_answer(full).spoken_clean or full
    db.add(AIAnswer(meeting_id=meeting.id, asked_by=user.id, question_text=q, answer_text=full, model="live", status="completed"))
    await db.commit()
    await asyncio.sleep(0.01)
    return spoken, full, ctx


results = []


def check(name, ok, spoken):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n     ANSWER: {spoken[:420].replace(chr(10), ' ')}\n")


async def main():
    async with SessionLocal() as db:
        user, ws = await ensure_dev_identity(db)
        ndocs = await db.scalar(select(func.count(Document.id)).where(Document.uploaded_by == user.id, Document.status == "ready"))
        kinds = (await db.execute(select(Document.doc_kind, Document.filename).where(Document.uploaded_by == user.id, Document.status == "ready"))).all()
        print("ready docs:", ndocs, kinds)
        m = await meeting_service.create_meeting(db, ws.id, user.id, "Live five-scenario check (safe to ignore)")
        db.add(TranscriptSegment(meeting_id=m.id, chunk_index=0, start_offset_ms=0, end_offset_ms=3000,
                                 text="Client said: the target warehouse is Redshift and the load cadence must be hourly."))
        await db.commit()
        t = time.time()

        print("\n1) DECISION MEMORY (user decision stated, then asked back)")
        await ask(db, m, user, "How would you load CDC data from PostgreSQL into the warehouse?")
        await ask(db, m, user, "Let's decide: we use AWS Glue for loading, not EMR, and MWAA for orchestration.")
        s, f, _ = await ask(db, m, user, "What did we decide for orchestration?")
        check("recalls MWAA decision", "mwaa" in s.lower() or "airflow" in s.lower(), s)

        print("2) PRONOUN FOLLOW-UP (no keywords, no clarify)")
        s, f, _ = await ask(db, m, user, "Why did we choose that over the other option?")
        check("explains Glue vs EMR, no Clarify", "clarify" not in s.lower() and "glue" in (s + f).lower(), s)

        print("3) CORRECTION MID-SESSION")
        await ask(db, m, user, "Actually change that, use Kinesis Data Firehose instead of Glue for the loading.")
        s, f, _ = await ask(db, m, user, "Which service does the loading now?")
        check("uses corrected choice (Firehose/Kinesis)", bool(re.search(r"(?i)firehose|kinesis", s)), s)

        print("4) TRANSCRIPT DECISION (said in meeting, not asked to bot)")
        s, f, _ = await ask(db, m, user, "How often should we schedule the load?")
        check("answers hourly from the transcript", "hour" in s.lower(), s)

        print("5) UPLOADED CV/JD USED ON A FOLLOW-UP")
        s1, f1, c1 = await ask(db, m, user, "Tell me about the most complex project you worked on.")
        print("   docs sent:", [(d.filename, d.doc_type) for d in c1.full_docs])
        s, f, c = await ask(db, m, user, "What was your exact role in that one?")
        print("   docs sent on follow-up:", [(d.filename, d.doc_type) for d in c.full_docs])
        check("follow-up keeps docs + context, no Clarify", len(c.full_docs) > 0 and "clarify" not in s.lower(), s)
        s, f, _ = await ask(db, m, user, "What does the job description say it needs from the cloud side?")
        check("reads the JD", len(c.full_docs) > 0 and bool(re.search(r"(?i)aws|azure|gcp|cloud", s)), s)

        mm = await db.get(Meeting, m.id)
        mm.status = "ended"
        await db.commit()
        print(f"{sum(results)}/{len(results)} passed in {time.time()-t:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())

"""Live latency check (real model, costs a few cents). Measures context build time, time to first word and total time
for a growing session (history + transcript + all documents).
Run:  .venv/Scripts/python.exe tests/live_latency.py"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.core.dev_auth import ensure_dev_identity  # noqa: E402
from app.models import AIAnswer, Meeting  # noqa: E402
from app.rag.pipeline import build_context  # noqa: E402
from app.services import meeting_service, openai_service  # noqa: E402

QUESTIONS = [
    "Tell me about yourself.",
    "How would you load CDC data from PostgreSQL into Redshift?",
    "Why did you choose that approach?",
    "How do you handle late arriving records?",
    "What was your role in the most complex project?",
    "What does the job description need from the cloud side?",
]


async def main():
    async with SessionLocal() as db:
        user, ws = await ensure_dev_identity(db)
        m = await meeting_service.create_meeting(db, ws.id, user.id, "Latency check (safe to ignore)")
        firsts = []
        for q in QUESTIONS:
            t0 = time.monotonic()
            ctx = await build_context(db, m, q, None, user_id=user.id)
            t_ctx = time.monotonic() - t0
            chars = sum(len(x["content"]) for x in ctx.messages)
            first, parts = None, []
            async for d in openai_service.stream_chat(ctx.messages):
                first = first or time.monotonic() - t0
                parts.append(d)
            total = time.monotonic() - t0
            firsts.append(first)
            db.add(AIAnswer(meeting_id=m.id, asked_by=user.id, question_text=q, answer_text="".join(parts),
                            model="live", status="completed"))
            await db.commit()
            print(f"ctx {t_ctx*1000:5.0f} ms | first word {first:5.1f} s | total {total:5.1f} s | prompt {chars:6d} chars | {q[:50]}")
        print(f"avg first word {sum(firsts)/len(firsts):.1f}s, worst {max(firsts):.1f}s")
        mm = await db.get(Meeting, m.id)
        mm.status = "ended"
        await db.commit()


if __name__ == "__main__":
    asyncio.run(main())

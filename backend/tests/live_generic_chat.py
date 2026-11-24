"""Live generic-chat check (real model, costs a few cents): the assistant must behave like a normal AI chat for ANY
document and ANY question — non-technical uploads, plain questions, writing tasks, follow-ups, session memory and a
document added mid-session. Uses temporary documents that are deleted afterwards.
Run:  .venv/Scripts/python.exe tests/live_generic_chat.py"""
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.dev_auth import ensure_dev_identity  # noqa: E402
from app.models import AIAnswer, Document, Meeting  # noqa: E402
from app.rag.pipeline import build_context  # noqa: E402
from app.services import document_service as ds, meeting_service, openai_service  # noqa: E402
from app.services.diagram_renderer import parse_structured_answer  # noqa: E402

LEASE = """RESIDENTIAL LEASE AGREEMENT
Landlord: Meera Kulkarni. Tenant: Arjun Rao. Property: Flat 4B, Lakeview Residency, Pune.
1. Term. The lease runs for 11 months starting 1 March 2026.
2. Rent. Monthly rent is INR 32,500, due on or before the 5th of each month. A refundable security deposit of INR 97,500 is held by the landlord.
3. Late payment. A late fee of INR 500 per day applies after the 7th of the month.
4. Termination. Either party may end the lease with 60 days written notice. Early exit by the tenant within the first 6 months forfeits one month of the deposit.
5. Maintenance. The tenant pays electricity, water and internet. The landlord pays building society charges and major repairs.
6. Subletting is not permitted without written consent of the landlord.
"""
REPORT = """QUARTERLY SALES REPORT Q3 FY26
Total revenue was USD 4.8 million, up 12 percent from Q2. The West region led with USD 2.1 million.
The East region declined 4 percent to USD 0.9 million because two distributors paused orders.
Customer churn fell from 6.1 percent to 4.7 percent. The top risk for Q4 is delayed shipments from the Vietnam plant.
Recommendation: add a second logistics partner before November.
"""

results: list[bool] = []


def check(name, ok, spoken):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n     ANSWER: {spoken[:400].replace(chr(10), ' ')}\n")


async def ask(db, meeting, user, q):
    ctx = await build_context(db, meeting, q, None, user_id=user.id)
    parts = []
    async for d in openai_service.stream_chat(ctx.messages):
        parts.append(d)
    full = "".join(parts)
    parsed = parse_structured_answer(full)
    db.add(AIAnswer(meeting_id=meeting.id, asked_by=user.id, question_text=q, answer_text=full, model="live",
                    status="completed"))
    await db.commit()
    await asyncio.sleep(0.01)
    return (parsed.spoken_clean or full), full


async def add_doc(db, user, ws, name, text, kind="supporting"):
    path = ds.store_file(ws.id, name, text.encode())
    doc = Document(workspace_id=ws.id, uploaded_by=user.id, filename=name, content_type="text/plain",
                   storage_path=path, size_bytes=len(text), file_sha256=ds.sha256_bytes(text.encode()) + uuid.uuid4().hex[:0],
                   doc_kind=kind, status="pending", embedding_status="pending", meta_json="{}")
    db.add(doc)
    await db.commit()
    await ds.ingest_document(doc.id)
    await db.refresh(doc)
    assert doc.status == "ready", doc.error
    return doc


async def main():
    docs = []
    async with SessionLocal() as db:
        user, ws = await ensure_dev_identity(db)
        m = await meeting_service.create_meeting(db, ws.id, user.id, "Generic chat check (safe to ignore)")
        try:
            lease = await add_doc(db, user, ws, "lease_agreement.txt", LEASE)
            docs.append(lease)
            m.document_ids_json = json.dumps([str(lease.id)])
            await db.commit()

            print("1) PLAIN QUESTION (no document needed)")
            s, f = await ask(db, m, user, "What is the difference between TCP and UDP?")
            check("answers TCP vs UDP properly", "tcp" in s.lower() and "udp" in s.lower() and "lease" not in s.lower(), s)

            print("2) SMALL TALK")
            s, f = await ask(db, m, user, "Hi, how are you today?")
            check("short friendly reply", 0 < len(s.split()) < 60 and "clarify" not in s.lower(), s)

            print("3) NON-TECHNICAL DOCUMENT, EXACT FACTS")
            s, f = await ask(db, m, user, "What is the monthly rent and the notice period to end the lease?")
            check("rent 32,500 and 60 days", "32,500" in s and "60" in s, s)

            print("4) FOLLOW-UP (memory + same document)")
            s, f = await ask(db, m, user, "And what happens if I pay late?")
            check("late fee 500 per day after the 7th", "500" in s and bool(re.search(r"7th|seventh|7", s)), s)

            print("5) NOT IN THE DOCUMENT")
            s, f = await ask(db, m, user, "Does the agreement allow me to keep pets?")
            check("says the agreement does not mention pets (no invention)",
                  bool(re.search(r"(?i)not (mention|say|specif|cover|address|include)|doesn.t (mention|say|specif|cover|address)|no mention|silent|nothing", s)), s)

            print("6) SUMMARY TASK (format and length follow the task)")
            s, f = await ask(db, m, user, "Summarize the agreement in 4 bullet points.")
            bullets = len(re.findall(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", f))
            check("about 4 bullets, covers key terms", 3 <= bullets <= 6 and "32,500" in f, f)

            print("7) WRITING TASK")
            s, f = await ask(db, m, user, "Write a short polite email to the landlord asking to extend the lease by one month.")
            check("an email (subject/greeting) mentioning the landlord", bool(re.search(r"(?i)subject|dear|hi |hello", f)) and "meera" in f.lower(), f)

            print("8) SESSION MEMORY OF FACTS")
            await ask(db, m, user, "Remember this: my name is Priya and my moving budget is 1200 dollars.")
            s, f = await ask(db, m, user, "What is my budget and what is my name?")
            check("recalls 1200 and Priya", "1200" in s.replace(",", "") and "priya" in s.lower(), s)

            print("9) DOCUMENT ADDED MID-SESSION")
            rep = await add_doc(db, user, ws, "q3_sales_report.txt", REPORT)
            docs.append(rep)
            m = await db.get(Meeting, m.id)
            m.document_ids_json = json.dumps([str(lease.id), str(rep.id)])
            await db.commit()
            s, f = await ask(db, m, user, "Which region led sales and what is the main Q4 risk?")
            check("West region and Vietnam shipments", "west" in s.lower() and "vietnam" in s.lower(), s)
            s, f = await ask(db, m, user, "Going back to the lease, how much is the security deposit?")
            check("switches back: deposit 97,500", "97,500" in s, s)
        finally:
            for d in docs:
                d2 = await db.get(Document, d.id)
                if d2 is not None:
                    path = d2.storage_path
                    await db.delete(d2)
                    await db.commit()
                    ds.remove_file(path)
            mm = await db.get(Meeting, m.id)
            mm.status = "ended"
            await db.commit()
        print(f"{sum(results)}/{len(results)} passed")


if __name__ == "__main__":
    asyncio.run(main())

"""Live answer evaluation (uses the real model — costs a few cents). Not part of the zero-cost test suite.

Runs varied, unseen questions through the real retrieval + prompt pipeline in a throwaway meeting whose transcript
contains an UNRELATED question, and applies automatic checks. Checks are heuristics: a pass does not prove an answer
is correct, so print the answers and read them too.

Run:  .venv\\Scripts\\python.exe tests\\eval_live_answers.py
"""
import asyncio
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.dev_auth import ensure_dev_identity  # noqa: E402
from app.models import AIAnswer, Meeting, TranscriptSegment  # noqa: E402
from app.rag.pipeline import build_context  # noqa: E402
from app.services import meeting_service, openai_service  # noqa: E402
from app.services.diagram_renderer import parse_structured_answer  # noqa: E402

META = re.compile(r"(?i)\b(the documents?|my cv|the resume|the material|the notes|the reference|architecture notes|"
                  r"described in|on record|work sample|the work i'm describing|from what i have)\b")
PERSONAL_CLAIM = re.compile(r"(?i)\b(rdsolutions|catalog categorizer|shelfgain|wockhardt|alpha ict)\b")

UNRELATED_TRANSCRIPT = [
    "Before we start, can you explain how Python decorators work?",
    "Also we might talk about Kubernetes autoscaling later.",
    "Okay let's move to the main topic now.",
]

DENIES = re.compile(r"(?i)\b(?:i\s+)?(?:haven[’']?t|have not|never|didn[’']?t|did not)\s+(?:used|worked|run|worked with|use)\b")
CLAIMS = re.compile(r"(?i)\bI\s+(?:used|implemented|built|ran|worked on|worked with|handled|managed)\b")


def MISSING_CHECKS(tool):  # noqa: N802
    return [
        ("flags experience as unconfirmed for the user", lambda s, f: "**check:**" in s.lower()),
        (f"does not deny {tool} experience", lambda s, f: not DENIES.search(s)),
        (f"does not claim {tool} experience",
         lambda s, f: not re.search(rf"(?i)\bI\s+(?:used|ran|implemented|built|managed)\b[^.]*\b{tool}\b", s)),
    ]


def ACRONYM_CHECKS(acr):  # noqa: N802
    return [
        ("asks what it stands for", lambda s, f: "clarify" in s.lower() and bool(re.search(r"(?i)stand for|mean", s))),
        ("offers no guessed meaning", lambda s, f: not re.search(rf"(?i)if you mean|stands for (?!here)|{acr}\s*\(|i\.e\.", s)),
        ("claims no experience with it", lambda s, f: not CLAIMS.search(s)),
        ("does not answer beyond the clarification", lambda s, f: len(s.split()) <= 40),
    ]


LONG_Q = ("Can you walk me through how you would design the end to end ingestion for about forty source systems, including "
          "the historical backfill from the on premise Oracle databases, the daily incremental loads using change data "
          "capture, the way you would handle schema evolution when a source team adds or renames columns, how you would "
          "reconcile row counts and control totals between source and Redshift, and how you would monitor freshness and "
          "alert the on call engineer when a load is late or fails?")

REAL_CARD_MIXED = ("Road counts null checks and reconciliation reports between the source system and Redshift, with "
                   "alerts in CloudWatch And how do you tune redshift performance? For example, distribution keys and "
                   "sort keys.")
REAL_FULL_CONVERSATION = (
    "Thanks for joining. Our client is migrating current and historical data from multiple sources into Amazon "
    "Redshift. Sure. I would start with an inventory of the sources, then land raw data in S3 and use AWS Glue for the "
    "transformations. How would you design incremental loads for tables that change every hour? I would use change "
    "data capture with AWS DMS. Write Parquet partitions to S3 and merge them into Redshift with staging tables. What "
    "would you do about data quality checks before the business uses the numbers? Road counts null checks and "
    "reconciliation reports between the source system and Redshift, with alerts in CloudWatch And how do you tune "
    "redshift performance? For example, distribution keys and sort keys.")
CLAIMS_AWS_PROD = re.compile(r"(?i)\bI\s+(?:built|implemented|migrated|ran|deployed|delivered)\b[^.]*\b"
                             r"(redshift|aws glue|dms|kinesis)\b[^.]*\b(in production|at|for (?:the|a) client)\b")
SINGLE_Q = lambda s, f: not re.search(r"(?i)\*\*q\d*:", s)  # noqa: E731

CASES = [
    # ---- production-like cards from the 2026-11-16 Live run (question-wise answering) ----
    ("card: own reply + new question", REAL_CARD_MIXED, None,
     [("answers the open question (dist/sort keys)", lambda s, f: "distribution" in s.lower() and "sort" in s.lower()),
      ("single open question -> no Q headings", SINGLE_Q),
      ("Redshift tuning is the main body", lambda s, f: s.lower().count("sort") >= 2)]),
    ("card: whole conversation, last Q open", REAL_FULL_CONVERSATION, None,
     [("answers all three questions separately (Q1..Q3)",
       lambda s, f: all(f"**q{i}" in s.lower() for i in (1, 2, 3))),
      ("Q1 builds on the user's DMS/CDC reply", lambda s, f: bool(re.search(r"(?i)dms|change data capture|cdc", s))),
      ("covers data quality", lambda s, f: bool(re.search(r"(?i)reconcil|null|quality", s))),
      ("covers Redshift tuning", lambda s, f: "distribution" in s.lower() and "sort" in s.lower()),
      ("no invented AWS production claim", lambda s, f: not CLAIMS_AWS_PROD.search(s))]),
    ("card: small talk + intro", "Hi Ravi, how are you doing today? Good. So tell me about yourself.", None,
     [      ("warm natural reply to how are you", lambda s, f: bool(re.search(r"(?i)doing (well|good|great)|i'?m good|"
                                                                     r"thanks for asking|i'?m well", s))),
      ("introduces as a data engineer", lambda s, f: bool(re.search(r"(?i)data engineer|data engineering", s)))]),
    ("card: one design question", "How would you design incremental loads for tables that change every hour?", None,
     [("covers CDC/watermark + merge", lambda s, f: bool(re.search(r"(?i)cdc|change data capture|watermark", f))
       and bool(re.search(r"(?i)merge|upsert", f))),
      ("idempotent / re-runnable", lambda s, f: bool(re.search(r"(?i)idempot|re-?run", f))),
      ("design principles in technical details", lambda s, f: bool(re.search(r"(?i)SOLID|single responsibility|"
                                                                           r"separation of concerns|open.closed", f))),
      ("no invented AWS production claim", lambda s, f: not CLAIMS_AWS_PROD.search(s))]),
    ("card: two open questions", "How do you handle schema evolution when a source adds columns? And what is your "
     "approach for masking PII in Redshift?", None,
     [("answers each question separately", lambda s, f: "**q1" in s.lower() and "**q2" in s.lower()),
      ("covers schema evolution and PII", lambda s, f: "schema" in s.lower() and bool(re.search(r"(?i)pii|mask", s)))]),

    # (name, question, selected_text, checks: list of (description, fn(spoken, full) -> bool))
    ("general concept, no CV", "What is the difference between a star schema and a snowflake schema?", None,
     [("no personal project names", lambda s, f: not PERSONAL_CLAIM.search(s)),
      ("answers star/snowflake", lambda s, f: "snowflake" in s.lower() and "star" in s.lower()),
      ("does not answer the transcript's decorator question", lambda s, f: "decorator" not in f.lower())]),
    ("AWS not forced into plain Python", "What is a Python generator and when would you use one?", None,
     [("no AWS mention", lambda s, f: "aws" not in f.lower()),
      ("mentions yield", lambda s, f: "yield" in f.lower())]),
    ("unseen personal question", "How did you handle duplicate records in your pipelines?", None,
     [("no meta wording", lambda s, f: not META.search(s)),
      ("no invented framework name", lambda s, f: not re.search(r"(?i)great expectations|deequ|soda", s) or "would" in s.lower())]),
    ("missing evidence (Snowflake)", "Tell me about your production experience with Snowflake.", None, MISSING_CHECKS("Snowflake")),
    ("missing evidence (Kafka)", "What was your experience running Kafka in production?", None, MISSING_CHECKS("Kafka")),
    ("unknown acronym (QRT)", "How did you use QRT in your last project?", None, ACRONYM_CHECKS("QRT")),
    ("unknown acronym (PFM)", "Did you work on the PFM module at your last company?", None, ACRONYM_CHECKS("PFM")),
    ("long question (494 chars)", LONG_Q, None,
     [("covers schema evolution", lambda s, f: "schema" in f.lower()),
      ("covers reconciliation", lambda s, f: "reconcil" in f.lower()),
      ("covers monitoring/alerting", lambda s, f: bool(re.search(r"(?i)alert|monitor", f)))]),
    ("two questions in one", "What is a data lakehouse? How is it different from a data warehouse?", None,
     [("answers both parts", lambda s, f: "lakehouse" in s.lower() and "warehouse" in s.lower())]),
    ("misheard transcript", "what is the defense between delta leak and parkay files", None,
     [("flags its reading or answers Delta Lake vs Parquet",
       lambda s, f: "reading the question as" in s.lower() or ("delta lake" in s.lower() and "parquet" in s.lower()))]),
    ("garbled transcript", "how the flubber gristle shimmer inside wazz pipeline with the tork", None,
     [("asks for clarification", lambda s, f: "clarify" in s.lower())]),
    ("most complex project", "What is the most complex Databricks solution you worked on or designed?", None,
     [("leads with implemented work", lambda s, f: bool(re.search(r"(?i)\b(built|developed|engineered|designed and delivered|worked on|delivered)\b", s.split("\n\n")[0]))),
      ("no meta wording", lambda s, f: not META.search(s))]),
    ("follow-up 1", "Design a pipeline to load CDC data from PostgreSQL into Redshift.", None,
     [("mentions CDC + Redshift", lambda s, f: "redshift" in f.lower() and "cdc" in f.lower())]),
    ("follow-up 2 (refers to previous)", "How would you handle late-arriving records in that pipeline?", None,
     [("stays on the CDC/Redshift pipeline", lambda s, f: bool(re.search(r"(?i)redshift|cdc|merge|watermark", f)))]),
]


async def main() -> int:
    async with SessionLocal() as db:
        user, ws = await ensure_dev_identity(db)
        meeting = await meeting_service.create_meeting(db, ws.id, user.id, "Answer eval (safe to ignore)")
        for i, t in enumerate(UNRELATED_TRANSCRIPT):
            db.add(TranscriptSegment(meeting_id=meeting.id, chunk_index=i, start_offset_ms=i * 2000,
                                     end_offset_ms=i * 2000 + 2000, text=t))
        await db.commit()
        failures = 0
        only = [x for x in os.getenv("EVAL_ONLY", "").split("|") if x]
        for name, q, sel, checks in CASES:
            if only and not any(o in name for o in only):
                continue
            ctx = await build_context(db, meeting, q, sel, user_id=user.id)
            t0 = time.time()
            first = None
            parts = []
            async for d in openai_service.stream_chat(ctx.messages):
                first = first or time.time() - t0
                parts.append(d)
            full = "".join(parts)
            spoken = parse_structured_answer(full).spoken_markdown
            # store as a completed answer so follow-ups see the previous turn
            db.add(AIAnswer(meeting_id=meeting.id, asked_by=user.id, question_text=q, answer_text=full,
                            model="eval", status="completed"))
            await db.commit()
            if os.getenv("EVAL_OUT"):
                with open(os.environ["EVAL_OUT"], "a", encoding="utf-8") as fh:
                    fh.write(f"\n\n######## {name}\nQ: {q}\n--- SPOKEN ---\n{spoken}\n--- FULL ---\n{full}\n")
            print(f"\n=== {name} | intent={ctx.intent} | first token {first:.1f}s, total {time.time()-t0:.1f}s")
            print(f"Q: {q[:200]}\nSPOKEN: {spoken[:1200]}")
            for desc, fn in checks:
                ok = bool(fn(spoken, full))
                failures += 0 if ok else 1
                print(f"  [{'PASS' if ok else 'FAIL'}] {desc}")
        meeting = await db.get(Meeting, meeting.id)
        meeting.status = "ended"
        await db.commit()
    print(f"\n{len(CASES)} cases, {failures} failed checks")
    return failures


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)

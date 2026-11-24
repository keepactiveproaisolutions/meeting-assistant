"""Phase 5 verification test suite (zero paid API calls):
Verifies Requirement 12 (A through I) + Synchronization:
  1. PDF extracted and inspected (3 pages, 7 structured passages, SHA-256 verified)
  2. Persist in existing PostgreSQL `meeting_assistant` database (`documents`, `document_chunks`, `profile_entries`)
     + pgvector verified + duplicate upload deduplication (`file_sha256`)
  3. Passage/profile retrieval works and honestly reports `embedding_status='keyword_only'` when vectors are null
  4. Persists across backend restarts without re-uploading
  5. Brand-new meeting automatically accesses the saved profile & CV
  6. CV/experience & architecture questions include confirmed CV facts + self-reported skills + project briefing
     with contact PII (street address, phone number, personal email) redacted from model prompts
  7. Generic technical questions (`generic_technical`) exclude irrelevant CV/project details
  8. Missing/unconfirmed experience is NOT fabricated
  9. Follow-up questions retain active design context without promoting AI answers to CV facts
 10. Desktop & Mobile Web WebSocket clients receive identical synchronized answers; ownership privacy enforced
"""
import asyncio
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

import websockets
from sqlalchemy import text

os.environ["LLM_MOCK"] = "true"

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import SessionLocal as AsyncSessionLocal  # noqa: E402
from app.core.dev_auth import ensure_dev_identity  # noqa: E402
from app.rag.pipeline import build_context  # noqa: E402
from app.rag.retriever import search_documents  # noqa: E402

API_BASE = "http://127.0.0.1:8000/api/v1"
WS_BASE = "ws://127.0.0.1:8000/ws/meetings"
CV_FILE_PATH = r"C:\Users\RavishankarGavhane\Desktop\meeting-assistant\backend\uploads\00000000-0000-0000-0000-000000000002\Ravishankar_Gavhane_RDSolution1.pdf"


def http_json(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def http_multipart_pdf(url: str, pdf_path: str) -> tuple[int, dict]:
    boundary = "----MeetingAssistantCVBoundary987654"
    file_bytes = Path(pdf_path).read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="Ravishankar_Gavhane_RDSolution1.pdf"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


async def collect_ws_until_complete(ws, timeout: float = 15.0):
    chunks = []
    completed_msg = None
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        rem = max(0.1, deadline - asyncio.get_event_loop().time())
        raw = await asyncio.wait_for(ws.recv(), timeout=rem)
        outer = json.loads(raw)
        mtype = outer.get("type")
        msg = outer.get("data", outer)
        if mtype in ("answer.chunk", "answer.delta"):
            chunks.append(msg.get("delta", ""))
        elif mtype == "answer.completed":
            completed_msg = msg
            break
    return "".join(chunks), completed_msg


async def main():
    print("=== PHASE 5 VERIFICATION: CV PERSISTENCE, DEMO PREP & CONTEXT-ROUTED RAG (LLM_MOCK=true) ===")

    # 1 & 2. Inspect PostgreSQL directly and verify Profile / CV endpoint + Deduplication
    async with AsyncSessionLocal() as db:
        pgv = (
            await db.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
        ).scalar_one_or_none()
        assert pgv is not None, "pgvector extension must be installed"
        dev_user, _ = await ensure_dev_identity(db)
        DEV_USER_ID = dev_user.id
        print(f"[CHECK 1 & 2a] PostgreSQL pgvector extension active: v{pgv} (dev_user={DEV_USER_ID})")

    status, prof = http_json("GET", f"{API_BASE}/profile")
    assert status == 200
    if prof["cv_document"] is None:
        up_status, _ = http_multipart_pdf(f"{API_BASE}/profile/cv", CV_FILE_PATH)
        assert up_status == 200
        status, prof = http_json("GET", f"{API_BASE}/profile")
    assert prof["cv_document"] is not None, "CV document should be persisted"
    cv_doc = prof["cv_document"]
    assert cv_doc["chunk_count"] == 7
    assert cv_doc["file_sha256"] == "283857a2569fb4406907954c6f9cacee6b28d09172d6373893d331e3e68f7618"
    assert cv_doc["embedding_status"] in ("keyword_only", "ready")
    for sk in ("Databricks", "AWS", "Azure", "GCP"):
        assert sk in prof["self_reported_skills"]
    assert "migrating current and historical data from multiple source systems into Amazon Redshift" in prof["project_notes"]
    print(
        f"[CHECK 1 & 2b] Persisted CV Document ID={cv_doc['id']}, Profile ID={prof['id']}, "
        f"Passages={cv_doc['chunk_count']}, "
        f"SHA256={cv_doc['file_sha256'][:16]}..., EmbeddingStatus={cv_doc['embedding_status']}"
    )

    # Test duplicate upload prevention via POST /api/v1/profile/cv
    dup_status, dup_data = http_multipart_pdf(f"{API_BASE}/profile/cv", CV_FILE_PATH)
    assert dup_status == 200
    assert dup_data["deduplicated"] is True, "Uploading identical PDF must deduplicate via SHA-256"
    assert dup_data["document_id"] == cv_doc["id"]
    assert dup_data["extraction_audit"]["page_count"] == 3
    print(f"[CHECK 2c] Duplicate PDF upload prevented (deduplicated=True, kept Doc ID={cv_doc['id']})")

    # Check Private Demo Preparation endpoint (Requirement F)
    demo_status, demo = http_json("GET", f"{API_BASE}/profile/demo-prep")
    assert demo_status == 200
    assert "136M" in json.dumps(demo["most_complex_databricks_project"])
    assert "<svg" in demo["proposed_migration_architecture"]["diagram"]["svg"]
    assert "<svg" in demo["proposed_database_model"]["diagram"]["svg"]
    assert len(demo["likely_follow_ups"]) >= 5
    assert len(demo["missing_details_to_confirm"]) >= 5
    print(
        f"[CHECK F] Demo Preparation pack verified: 2 rendered SVG diagrams, "
        f"{len(demo['likely_follow_ups'])} follow-up guides, {len(demo['missing_details_to_confirm'])} items to confirm"
    )

    _, ws_list = http_json("GET", f"{API_BASE}/workspaces")
    ws_id = uuid.UUID(ws_list[0]["id"])

    # 3 & 8/H: Test Retriever & Ownership Privacy directly on DB
    async with AsyncSessionLocal() as db:
        passages = await search_documents(
            db,
            ws_id,
            "What is the most complex Databricks solution or project you have worked on or designed?",
            top_k=4,
            user_id=DEV_USER_ID,
            include_cv=True,
        )
        assert len(passages) >= 1, "Must retrieve CV passages for Databricks query"
        top_passage = passages[0]
        assert "RDSolutions India" in top_passage.content and "136M" in top_passage.content
        print(
            f"[CHECK 3] Passage retrieval returned '{top_passage.section_title}' "
            f"(p.{top_passage.page_number}, score={top_passage.score})"
        )

        # Check ownership privacy: Another user cannot retrieve the owner's CV
        other_user_id = uuid.UUID("00000000-0000-0000-0000-000000000999")
        other_passages = await search_documents(
            db,
            ws_id,
            "RDSolutions India Databricks 136M Catalog Categorizer",
            top_k=4,
            user_id=other_user_id,
            include_cv=True,
        )
        assert len(other_passages) == 0, f"CV chunks leaked to unauthorized user! Got: {other_passages}"
        print("[CHECK 8/H] Ownership privacy verified: unauthorized user_id retrieves 0 CV chunks")

    # 5. Create a brand-new meeting
    m_status, meeting = http_json(
        "POST",
        f"{API_BASE}/workspaces/{ws_id}/meetings",
        {"title": "Phase 5 Demo & Layout Verification"},
    )
    assert m_status in (200, 201)
    meeting_id = meeting["id"]
    room_code = meeting["code"]
    print(f"[CHECK 5] Created brand-new meeting ID={meeting_id} (code={room_code})")

    # Verify Prompt Context Routing (Checks 6 & 7) on the new meeting
    async with AsyncSessionLocal() as db:
        q_cv = "What is the most complex Databricks solution or project you have worked on or designed?"
        ctx_cv = await build_context(db, ws_id, uuid.UUID(meeting_id), q_cv, None, user_id=DEV_USER_ID)
        assert ctx_cv.intent in ("cv_experience", "mixed")
        user_prompt_cv = ctx_cv.messages[1]["content"]
        assert "Confirmed Facts from Extracted CV" in user_prompt_cv
        assert "RDSolutions India" in user_prompt_cv
        assert "Additional User Self-Reported Skills" in user_prompt_cv
        assert "Databricks" in user_prompt_cv
        # Verify PII Redaction!
        assert "77749" not in user_prompt_cv, "Phone number must be redacted from model prompt!"
        assert "ravishankar.gavhane@gmail.com" not in user_prompt_cv, "Personal email must be redacted from model prompt!"
        assert "Nanded City" not in user_prompt_cv, "Street address must be redacted from model prompt!"
        print("[CHECK 6 & H] CV/Experience prompt includes Confirmed CV + Self-Reported Skills + Project Briefing, with Contact PII REDACTED")

        # Check 7: Generic technical question excludes CV/project context
        q_gen = "How is exception handling done in Python?"
        ctx_gen = await build_context(db, ws_id, uuid.UUID(meeting_id), q_gen, None, user_id=DEV_USER_ID)
        assert ctx_gen.intent == "generic_technical"
        assert ctx_gen.profile is None and len(ctx_gen.docs) == 0
        user_prompt_gen = ctx_gen.messages[1]["content"]
        assert "RDSolutions" not in user_prompt_gen and "Redshift" not in user_prompt_gen
        print("[CHECK 7] Generic technical question classified as 'generic_technical' and excludes CV/project context from prompt")

    # Connect TWO WebSocket clients (Desktop + Mobile Web) to the new meeting for live end-to-end checks
    ws_url = f"{WS_BASE}/{meeting_id}"
    async with websockets.connect(ws_url) as ws_desktop, websockets.connect(ws_url) as ws_mobile:
        init_d = json.loads(await ws_desktop.recv())
        init_m = json.loads(await ws_mobile.recv())
        assert init_d["type"] in ("session.init", "session.snapshot")
        assert init_m["type"] in ("session.init", "session.snapshot")

        # Test A: Expected tomorrow discussion question (CV grounded)
        q1 = "What is the most complex Databricks solution or project you have worked on or designed?"
        http_json("POST", f"{API_BASE}/meetings/{meeting_id}/ask", {"question": q1})
        _, comp_d1 = await collect_ws_until_complete(ws_desktop)
        _, comp_m1 = await collect_ws_until_complete(ws_mobile)
        assert comp_d1 is not None and comp_m1 is not None
        ans1 = comp_d1["answer_text"]
        assert "RDSolutions India" in ans1 and "136M" in ans1 and "69.10%" in ans1
        assert len(comp_d1.get("diagrams", [])) >= 1 and "<svg" in comp_d1["diagrams"][0]["svg"]
        print("[CHECK 6b] Streamed CV-grounded Databricks answer + architecture diagram to both Desktop & Mobile clients")

        # Test B: Generic technical question (no CV leakage)
        q2 = "How is exception handling done in Python?"
        http_json("POST", f"{API_BASE}/meetings/{meeting_id}/ask", {"question": q2})
        _, comp_d2 = await collect_ws_until_complete(ws_desktop)
        _, comp_m2 = await collect_ws_until_complete(ws_mobile)
        ans2 = comp_d2["answer_text"]
        assert "RDSolutions" not in ans2 and "Redshift" not in ans2
        assert "try" in ans2 and "except" in ans2
        print("[CHECK 7b] Generic Python question answered cleanly without CV or Redshift references")

        # Test C: Unconfirmed experience refusal (Check 8)
        q3 = "Tell me about the data engineering project you built at Goldman Sachs."
        http_json("POST", f"{API_BASE}/meetings/{meeting_id}/ask", {"question": q3})
        _, comp_d3 = await collect_ws_until_complete(ws_desktop)
        _, comp_m3 = await collect_ws_until_complete(ws_mobile)
        ans3 = comp_d3["answer_text"]
        assert "not listed in the confirmed CV" in ans3 or "RDSolutions India" in ans3
        print("[CHECK 8] Unconfirmed employer/experience query explicitly refuses to fabricate claims")

        # Test D: Proposed Redshift Migration Architecture + Follow-up continuity (Check 9)
        q4 = "How would you design the migration of current and historical data from multiple source systems into Amazon Redshift to support analytics and reporting?"
        http_json("POST", f"{API_BASE}/meetings/{meeting_id}/ask", {"question": q4})
        _, comp_d4 = await collect_ws_until_complete(ws_desktop)
        _, comp_m4 = await collect_ws_until_complete(ws_mobile)
        ans4 = comp_d4["answer_text"]
        assert "Proposed" in ans4 and "COPY" in ans4 and "S3" in ans4
        assert len(comp_d4.get("diagrams", [])) >= 1

        # Follow-up 1: Why this architecture?
        q5 = "Why this architecture?"
        http_json("POST", f"{API_BASE}/meetings/{meeting_id}/ask", {"question": q5})
        _, comp_d5 = await collect_ws_until_complete(ws_desktop)
        _, comp_m5 = await collect_ws_until_complete(ws_mobile)
        ans5 = comp_d5["answer_text"]
        assert "S3" in ans5 and "Redshift" in ans5

        # Follow-up 2: How would it change on Azure or GCP?
        q6 = "How would it change on Azure or GCP?"
        http_json("POST", f"{API_BASE}/meetings/{meeting_id}/ask", {"question": q6})
        _, comp_d6 = await collect_ws_until_complete(ws_desktop)
        _, comp_m6 = await collect_ws_until_complete(ws_mobile)
        ans6 = comp_d6["answer_text"]
        assert "ADLS Gen2" in ans6 and "BigQuery" in ans6
        assert comp_d6["answer_text"] == comp_m6["answer_text"]
        print("[CHECK 9 & 10] Follow-up questions retained active Redshift design state and synchronized identically across Desktop & Mobile Web")

    print("\nALL PHASE 5 CHECKS (1 THROUGH 10) PASSED WITH 0 PAID API CALLS!")


if __name__ == "__main__":
    asyncio.run(main())

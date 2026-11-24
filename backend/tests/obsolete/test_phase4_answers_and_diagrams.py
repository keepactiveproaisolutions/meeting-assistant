"""Comprehensive zero-cost verification suite for Phase 4:
- Safe Markdown & structured answer parsing (Spoken / Technical details / How to draw it / Design state)
- Architecture (flowchart) & Database (erDiagram) SVG rendering + invalid syntax fallback
- Malicious HTML / XSS / remote-image / Mermaid click sanitization
- CV & Project Notes API + truthful grounding boundary
- Follow-up design continuity across turns
- Multi-client WebSocket streaming sync (Desktop + Mobile Web) & reload restoration
"""
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

import websockets

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.services.diagram_renderer import (  # noqa: E402
    parse_structured_answer,
    sanitize_markdown_for_qt,
)
from app.services.mock_fixtures import (  # noqa: E402
    ARCHITECTURE,
    ER,
    LLD_ANSWER,
    MALICIOUS,
    PLAIN,
)

BASE_URL = "http://127.0.0.1:8000"
WS_BASE = "ws://127.0.0.1:8000"


def http_json(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


async def run_all_tests() -> None:
    s = get_settings()
    assert s.llm_mock is True, "Guardrail: LLM_MOCK must be True so zero paid OpenAI calls are made!"
    print("[PASS] 0. Guardrail verified: LLM_MOCK=True (0 paid API calls).")

    # 1. Plain spoken + Markdown table + code (no mandatory 4-heading template)
    p_plain = parse_structured_answer(PLAIN, is_complete=True)
    for forbidden in ("Direct Answer / Concept", "Key Mechanics / Architecture", "Concrete Example", "Best Practice / Takeaway"):
        assert forbidden not in PLAIN, f"Legacy template heading still present: {forbidden}"
    assert "Exception handling in Python" in p_plain.spoken_markdown
    assert "| Clause | Runs when | Typical use |" in p_plain.technical_markdown
    assert len(p_plain.code_blocks) == 1 and p_plain.code_blocks[0]["lang"] == "python"
    assert "    try:\n        with open" in p_plain.code_blocks[0]["code"], "Code indentation must be preserved"
    print("[PASS] 1. Answer style & Markdown table/code indentation verified.")

    # 2. Partial streamed Markdown handling
    partial_stream = ARCHITECTURE[: ARCHITECTURE.index("```", ARCHITECTURE.index("```mermaid") + 10)]
    p_partial = parse_structured_answer(partial_stream, is_complete=False)
    assert p_partial.pending_diagram is True
    assert len(p_partial.diagrams) == 0, "Unclosed mermaid fence must not render a broken partial diagram"
    assert "*(Rendering diagram…)*" in (p_partial.spoken_markdown + p_partial.technical_markdown)
    p_complete = parse_structured_answer(ARCHITECTURE, is_complete=True)
    assert p_complete.pending_diagram is False
    assert len(p_complete.diagrams) == 1 and p_complete.diagrams[0].ok is True
    print("[PASS] 2. Partial streamed Markdown holds back unclosed diagram until fence closes.")

    # 3. Valid Architecture diagram + How to draw it + Design State
    arch_diag = p_complete.diagrams[0]
    assert arch_diag.kind == "flowchart"
    assert arch_diag.svg.startswith("<svg") and arch_diag.svg.endswith("</svg>")
    assert "Web shop" in arch_diag.svg and "ERP database" in arch_diag.svg and "Reporting warehouse" in arch_diag.svg
    assert "1. Left: draw a *Sources* box" in p_complete.how_to_draw_markdown
    assert p_complete.design_state and p_complete.design_state.get("platform") == "cloud-neutral"
    print("[PASS] 3. Architecture flowchart SVG + 'How to draw it' + design-state verified.")

    # 4. Valid ER diagram + matching SQL DDL
    p_er = parse_structured_answer(ER, is_complete=True)
    assert len(p_er.diagrams) == 1
    er_diag = p_er.diagrams[0]
    assert er_diag.ok is True and er_diag.kind == "erDiagram"
    assert "DIM_CUSTOMER" in er_diag.svg and "FACT_ORDER_LINE" in er_diag.svg and "PK" in er_diag.svg and "FK" in er_diag.svg
    assert any(cb["lang"] == "sql" and "CREATE TABLE fact_order_line" in cb["code"] for cb in p_er.code_blocks)
    print("[PASS] 4. Database ER diagram SVG (PK/FK/types/cardinalities) + SQL DDL verified.")

    # 5. Low-level design (LLD)
    p_lld = parse_structured_answer(LLD_ANSWER, is_complete=True)
    assert "OrderValidator" in p_lld.technical_markdown and "OrderSink" in p_lld.technical_markdown
    assert len(p_lld.code_blocks) == 1 and "Protocol" in p_lld.code_blocks[0]["code"]
    print("[PASS] 5. Low-Level Design (LLD) contracts & typed code verified.")

    # 6. Malicious input & invalid diagram fallback
    p_mal = parse_structured_answer(MALICIOUS, is_complete=True)
    assert len(p_mal.diagrams) == 2
    d_xss, d_broken = p_mal.diagrams[0], p_mal.diagrams[1]
    assert d_xss.ok is True
    assert "<script>" not in d_xss.svg and "&lt;script&gt;alert(3)&lt;/script&gt;" in d_xss.svg
    assert "click A" not in d_xss.svg and "alert(4)" not in d_xss.svg
    assert d_broken.ok is False and d_broken.error
    qt_safe = sanitize_markdown_for_qt(p_mal.spoken_markdown)
    assert "<script>" not in qt_safe and "<img" not in qt_safe
    assert "javascript:alert" not in qt_safe and "http://evil.example/track.png" not in qt_safe
    print("[PASS] 6. Malicious HTML, XSS, unsafe links, remote images, and invalid Mermaid fallback verified.")

    # 7. Live Backend API & Multi-client WebSocket synchronization test
    _, cfg = http_json("GET", "/api/v1/auth/config")
    assert cfg["dev_auth_bypass"] is True and cfg["allow_lan_dev"] is True
    print(f"[PASS] 7a. Auth/LAN config verified: lan_web_url={cfg.get('lan_web_url')}")

    # Test CV / Profile grounding when empty vs populated
    http_json("PUT", "/api/v1/profile", {"cv_text": "", "project_notes": ""})
    # Join existing meeting SWL34JVU so the live answers are also visible in the user's open session!
    _, m_res = http_json("POST", "/api/v1/meetings/join", {"code": "SWL34JVU"})
    mid = m_res["id"]

    ws_url = f"{WS_BASE}/ws/meetings/{mid}"
    async with websockets.connect(ws_url) as ws_desktop, websockets.connect(ws_url) as ws_mobile:
        snap1 = json.loads(await asyncio.wait_for(ws_desktop.recv(), timeout=5))
        snap2 = json.loads(await asyncio.wait_for(ws_mobile.recv(), timeout=5))
        assert snap1["type"] == "session.snapshot" and snap2["type"] == "session.snapshot"

        # Ask architecture question
        status_code, ask1 = http_json(
            "POST",
            f"/api/v1/meetings/{mid}/ask",
            {"question": "Design an order ingestion pipeline and draw the architecture diagram."},
        )
        assert status_code == 202
        aid1 = ask1["answer_id"]

        desktop_events = []
        mobile_events = []

        async def collect_until_complete(ws, bucket):
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                bucket.append(msg)
                if msg["type"] == "answer.completed" and msg["data"]["answer_id"] == aid1:
                    return msg["data"]

        done_desktop, done_mobile = await asyncio.gather(
            collect_until_complete(ws_desktop, desktop_events),
            collect_until_complete(ws_mobile, mobile_events),
        )

        assert done_desktop["answer_text"] == done_mobile["answer_text"]
        assert len(done_desktop["diagrams"]) == 1 and done_desktop["diagrams"][0]["ok"] is True
        assert any(e["type"] == "answer.diagram" for e in desktop_events)
        assert any(e["type"] == "answer.diagram" for e in mobile_events)
        print("[PASS] 7b. Multi-client WebSocket sync (Desktop + Mobile Web) verified for streamed answer + diagram.")

        # Ask follow-up question ("How would this change on Azure and what if ingestion fails?")
        _, ask2 = http_json(
            "POST",
            f"/api/v1/meetings/{mid}/ask",
            {"question": "How would this change on Azure and what happens if ingestion fails?"},
        )
        aid2 = ask2["answer_id"]
        while True:
            msg = json.loads(await asyncio.wait_for(ws_desktop.recv(), timeout=15))
            if msg["type"] == "answer.completed" and msg["data"]["answer_id"] == aid2:
                assert msg["data"]["design_state"]["platform"] == "Azure"
                assert "Dead-letter queue" in msg["data"]["answer_text"]
                break
        print("[PASS] 7c. Follow-up continuity (cloud-neutral -> Azure + DLQ) verified.")

        # Ask Database ER Diagram & DDL question
        _, ask_er = http_json(
            "POST",
            f"/api/v1/meetings/{mid}/ask",
            {"question": "Now show the database design and ER diagram for the order warehouse."},
        )
        aid_er = ask_er["answer_id"]
        while True:
            msg = json.loads(await asyncio.wait_for(ws_desktop.recv(), timeout=15))
            if msg["type"] == "answer.completed" and msg["data"]["answer_id"] == aid_er:
                assert len(msg["data"]["diagrams"]) == 1
                assert msg["data"]["diagrams"][0]["kind"] == "erDiagram"
                break
        print("[PASS] 7d. Live streamed ER diagram & SQL DDL verified.")

        # Test CV boundary when empty
        _, ask3 = http_json(
            "POST",
            f"/api/v1/meetings/{mid}/ask",
            {"question": "Based on my CV and background, what projects have you built?"},
        )
        aid3 = ask3["answer_id"]
        while True:
            msg = json.loads(await asyncio.wait_for(ws_desktop.recv(), timeout=15))
            if msg["type"] == "answer.completed" and msg["data"]["answer_id"] == aid3:
                assert "won't invent first-person" in msg["data"]["answer_text"]
                break

        # Now save confirmed CV and ask again
        http_json(
            "PUT",
            "/api/v1/profile",
            {
                "cv_text": "Senior Data & Backend Engineer — 6 years building Python, FastAPI, Kafka, and PostgreSQL pipelines.",
                "project_notes": "Project Atlas: real-time order telemetry processing 50k events/day on Azure Event Hubs and PostgreSQL.",
            },
        )
        _, ask4 = http_json(
            "POST",
            f"/api/v1/meetings/{mid}/ask",
            {"question": "Based on my CV and background, summarize my experience."},
        )
        aid4 = ask4["answer_id"]
        while True:
            msg = json.loads(await asyncio.wait_for(ws_desktop.recv(), timeout=15))
            if msg["type"] == "answer.completed" and msg["data"]["answer_id"] == aid4:
                assert "Senior Data & Backend Engineer" in msg["data"]["answer_text"]
                assert "proposed design" in msg["data"]["answer_text"]
                break
        print("[PASS] 7e. CV & Project Notes grounding (empty refusal vs confirmed CV boundary) verified.")

    # Reload restoration check via GET /api/v1/meetings/{mid}
    _, reloaded = http_json("GET", f"/api/v1/meetings/{mid}")
    reloaded_a1 = next(a for a in reloaded["answers"] if a["id"] == aid1)
    assert reloaded_a1["spoken_markdown"] == done_desktop["spoken_markdown"]
    assert reloaded_a1["technical_markdown"] == done_desktop["technical_markdown"]
    assert reloaded_a1["how_to_draw_markdown"] == done_desktop["how_to_draw_markdown"]
    assert len(reloaded_a1["diagrams"]) == 1 and reloaded_a1["diagrams"][0]["svg"].startswith("<svg")
    print("[PASS] 7f. Reload restoration matches streamed answer and diagrams byte-for-byte.")

    print("\n=== ALL PHASE 4 VERIFICATION TESTS PASSED (0 PAID API CALLS) ===")


if __name__ == "__main__":
    asyncio.run(run_all_tests())

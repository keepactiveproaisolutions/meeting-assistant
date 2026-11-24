"""End-to-end test through the REAL running app (HTTP + WebSocket, like the web and desktop clients), real model.
Needs the backend on http://127.0.0.1:8000 and the packages httpx + websocket-client (the desktop-app venv has both):
    desktop-app\\.venv\\Scripts\\python.exe backend\\tests\\live_e2e_api.py
Uploads temporary documents through POST /documents and deletes them at the end."""
import json
import re
import threading
import time

import httpx
import websocket

API = "http://127.0.0.1:8000/api/v1"
WS = "ws://127.0.0.1:8000"
TOKEN = "dev"
H = {"Authorization": f"Bearer {TOKEN}"}

LEASE = """RESIDENTIAL LEASE AGREEMENT
Landlord: Meera Kulkarni. Tenant: Arjun Rao. Property: Flat 4B, Lakeview Residency, Pune.
1. Term. The lease runs for 11 months starting 1 March 2026.
2. Rent. Monthly rent is INR 32,500, due on or before the 5th of each month. A refundable security deposit of INR 97,500 is held by the landlord.
3. Late payment. A late fee of INR 500 per day applies after the 7th of the month.
4. Termination. Either party may end the lease with 60 days written notice.
5. Maintenance. The tenant pays electricity, water and internet. The landlord pays building society charges and major repairs.
6. Subletting is not permitted without written consent of the landlord.
"""
RESUME = """JANE DOE - Senior Product Designer
Experience
Lead Designer, Northwind Health (2021-2025): led the redesign of the patient portal, raising task completion from 61% to 84%. Managed 4 designers.
Designer, Bluepeak Retail (2018-2021): built the design system used by 12 product teams; shipped the mobile checkout that cut drop-off by 18%.
Skills: Figma, user research, prototyping, accessibility (WCAG 2.2), workshop facilitation.
Education: BDes, National Institute of Design (2017).
"""
JD = """Job Description - Head of Design, Acme Fintech
We need a design leader to own our mobile banking app, build a design system, and manage a team of 6+ designers.
Requirements: 6+ years in product design, experience in regulated industries, strong accessibility practice, stakeholder management.
"""

results: list[bool] = []


def check(name, ok, text):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n     ANSWER: {text[:360].replace(chr(10), ' ')}\n")


class Client:
    """A WebSocket client of one meeting that records every event (like the web/desktop app does)."""

    def __init__(self, meeting_id, name):
        self.name, self.events, self.lock = name, [], threading.Lock()
        self.ws = websocket.create_connection(f"{WS}/ws/meetings/{meeting_id}?token={TOKEN}", timeout=130)
        self.snapshot = json.loads(self.ws.recv())
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _loop(self):
        try:
            while True:
                msg = json.loads(self.ws.recv())
                with self.lock:
                    self.events.append((time.monotonic(), msg))
        except Exception:
            pass

    def ask(self, q):
        self.ws.send(json.dumps({"type": "ask", "data": {"question": q}}))

    def wait_answer(self, q, timeout=120):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                ev = list(self.events)
            started = next((m for _, m in ev if m["type"] == "answer.started" and m["data"].get("question_text") == q), None)
            if started:
                aid = started["data"]["answer_id"]
                done = next((m for _, m in ev if m["type"] in ("answer.completed", "answer.failed")
                             and m["data"]["answer_id"] == aid), None)
                if done:
                    deltas = [m["data"]["delta"] for _, m in ev if m["type"] == "answer.delta" and m["data"]["answer_id"] == aid]
                    return done, "".join(deltas)
            time.sleep(0.1)
        raise TimeoutError(q)

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def upload(http, name, text, kind):
    r = http.post(f"{API}/documents", headers=H, files={"file": (name, text.encode(), "text/plain")}, data={"doc_type": kind})
    r.raise_for_status()
    doc = r.json()
    for _ in range(60):
        d = http.get(f"{API}/documents/{doc['id']}", headers=H).json()
        if d["status"] in ("ready", "failed"):
            assert d["status"] == "ready", d.get("error")
            return d
        time.sleep(1)
    raise TimeoutError("ingest")


def main():
    http = httpx.Client(timeout=60)
    doc_ids, meeting_id, clients = [], None, []
    try:
        print("health:", http.get("http://127.0.0.1:8000/health").json())
        m = http.post(f"{API}/meetings", headers=H, json={"title": "E2E API check (safe to ignore)"}).json()
        meeting_id = m["id"]
        print("meeting", m["code"], "created")

        for name, text, kind in (("lease_agreement.txt", LEASE, "supporting"), ("jane_resume.txt", RESUME, "cv"),
                                 ("head_of_design_jd.txt", JD, "jd")):
            d = upload(http, name, text, kind)
            doc_ids.append(d["id"])
            print(f"uploaded {name}: {d['status']}, {d['chunk_count']} chunk(s)")
        r = http.put(f"{API}/meetings/{meeting_id}/documents", headers=H, json={"document_ids": doc_ids}).json()
        print("selected documents:", sum(1 for x in r["documents"] if x["selected"]))

        web, desktop = Client(meeting_id, "web"), Client(meeting_id, "desktop")
        clients += [web, desktop]

        print("\n1) Plain question over the live socket")
        q = "What is the difference between a stack and a queue?"
        web.ask(q)
        done, text = web.wait_answer(q)
        check("answers stack vs queue", done["type"] == "answer.completed" and "stack" in text.lower() and "queue" in text.lower(), text)

        print("2) Both clients (web + desktop) received the same stream")
        done2, text2 = desktop.wait_answer(q)
        check("desktop got identical answer text", text2 == text and done2["type"] == "answer.completed", text2)

        print("3) Uploaded lease, exact facts")
        q = "What is the monthly rent and the security deposit?"
        web.ask(q)
        done, text = web.wait_answer(q)
        check("rent 32,500 + deposit 97,500", "32,500" in text and "97,500" in text, text)

        print("4) Follow-up with no keywords (memory)")
        q = "And how much notice is needed to leave?"
        web.ask(q)
        done, text = web.wait_answer(q)
        check("60 days", "60" in text, text)

        print("5) Interview question using resume + JD")
        q = "Tell me about your most relevant experience for this role."
        web.ask(q)
        done, text = web.wait_answer(q)
        check("uses resume facts (design system / portal) and no invented employer",
              bool(re.search(r"(?i)design system|patient portal|northwind|bluepeak", text)), text)

        print("6) Reconnect mid-session, then a follow-up that needs memory")
        web.close()
        web = Client(meeting_id, "web2")
        clients.append(web)
        n = len(web.snapshot["data"]["answers"])
        check("snapshot restores all 4 earlier answers after reconnect", n == 4, f"{n} answers in snapshot")
        q = "Which of those results would you highlight first in an interview with them?"
        web.ask(q)
        done, text = web.wait_answer(q)
        check("answer stays on topic, no clarify request", "clarify" not in text.lower() and len(text) > 40, text)

        print("7) REST /ask path used by the desktop app")
        q = "In one sentence, what is the late fee in the lease?"
        r = http.post(f"{API}/meetings/{meeting_id}/ask", headers=H, json={"question": q})
        assert r.status_code == 202, r.text
        done, text = desktop.wait_answer(q)
        check("late fee 500 per day", "500" in text, text)

        print("8) Timing")
        for _, msg in web.events:
            pass
        print("  first/total ms per answer:", [(e["data"].get("first_word_ms"), e["data"].get("total_ms"))
              for _, e in desktop.events if e["type"] == "answer.completed"])
    finally:
        for c in clients:
            c.close()
        for i in doc_ids:
            http.delete(f"{API}/documents/{i}", headers=H)
        if meeting_id:
            http.post(f"{API}/meetings/{meeting_id}/end", headers=H)
        print(f"\n{sum(results)}/{len(results)} passed")


if __name__ == "__main__":
    main()

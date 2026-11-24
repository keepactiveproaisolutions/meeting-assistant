"""Multi-device, multi-account session test with REAL login (dev bypass OFF), real model.

Scenario: the owner creates a meeting (desktop), a second account joins with the meeting code / QR link (phone), a third
account that never joined tries to get in. Everything must behave as ONE shared session: transcript, questions, answers,
screen answers, document selection and meeting end are synchronised, and answers always use the OWNER's documents.

It starts its own backend on port 8010 (does not touch a running one) and removes the test users afterwards.
Run:  backend\\.venv\\Scripts\\python.exe backend\\tests\\live_multiplatform_session.py"""
import io
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx
import websocket
from PIL import Image, ImageDraw, ImageFont

BACKEND = Path(__file__).resolve().parents[1]
PORT = 8010
API = f"http://127.0.0.1:{PORT}/api/v1"
WS = f"ws://127.0.0.1:{PORT}"
RUN = uuid.uuid4().hex[:8]
results: list[bool] = []

LEASE = """RESIDENTIAL LEASE AGREEMENT. Landlord: Meera Kulkarni. Tenant: Arjun Rao.
Monthly rent is INR 32,500, due on or before the 5th of each month. A refundable security deposit of INR 97,500 is held.
Either party may end the lease with 60 days written notice. A late fee of INR 500 per day applies after the 7th."""
PARTICIPANT_DOC = "PRIVATE NOTES of the second account. The secret project codename is ORCHID-7731."


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n     {str(detail)[:300]!s}" if detail != "" else ""))


class Device:
    """One connected device (desktop / phone / web) of a meeting."""

    def __init__(self, name, meeting_id, token):
        self.name, self.events, self.lock = name, [], threading.Lock()
        self.ws = websocket.create_connection(f"{WS}/ws/meetings/{meeting_id}?token={token}", timeout=150)
        self.snapshot = json.loads(self.ws.recv())
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        try:
            while True:
                m = json.loads(self.ws.recv())
                with self.lock:
                    self.events.append(m)
        except Exception:
            pass

    def answer(self, aid, timeout=150):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                ev = list(self.events)
            done = next((m for m in ev if m["type"] in ("answer.completed", "answer.failed") and m["data"]["answer_id"] == aid), None)
            if done:
                return done["data"].get("answer_text", ""), done["type"]
            time.sleep(0.1)
        raise TimeoutError(aid)

    def ask(self, q):
        self.ws.send(json.dumps({"type": "ask", "data": {"question": q}}))
        end = time.monotonic() + 30
        while time.monotonic() < end:
            with self.lock:
                s = next((m for m in self.events if m["type"] == "answer.started" and m["data"].get("question_text") == q), None)
            if s:
                return s["data"]["answer_id"]
            time.sleep(0.1)
        raise TimeoutError(q)

    def has_event(self, kind, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                if any(m["type"] == kind for m in self.events):
                    return True
            time.sleep(0.1)
        return False

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def screenshot(lines):
    img = Image.new("RGB", (1300, 520), (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("arial.ttf", 30)
    except OSError:
        f = ImageFont.load_default()
    for i, line in enumerate(lines):
        d.text((40, 40 + i * 50), line, fill=(20, 20, 20), font=f)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def register(http, label):
    r = http.post(f"{API}/auth/register", json={"email": f"ma-test-{RUN}-{label}@example.com", "password": "TestPass#12345",
                                                "full_name": f"Test {label}"})
    r.raise_for_status()
    return r.json()["access_token"], r.json()["user"]["id"]


def upload(http, token, name, text):
    r = http.post(f"{API}/documents", headers={"Authorization": f"Bearer {token}"},
                  files={"file": (name, text.encode(), "text/plain")}, data={"doc_type": "supporting"})
    r.raise_for_status()
    doc = r.json()
    for _ in range(60):
        d = http.get(f"{API}/documents/{doc['id']}", headers={"Authorization": f"Bearer {token}"}).json()
        if d["status"] == "ready":
            return d["id"]
        time.sleep(1)
    raise TimeoutError("ingest")


def main():
    env = {**os.environ, "DEV_AUTH_BYPASS": "false", "PYTHONIOENCODING": "utf-8"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
                              cwd=str(BACKEND), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    http = httpx.Client(timeout=90)
    devices, user_ids = [], []
    try:
        for _ in range(60):
            try:
                if http.get(f"http://127.0.0.1:{PORT}/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        cfg = http.get(f"{API}/auth/config").json()
        check("server runs with real authentication (dev bypass off)", cfg["dev_auth_bypass"] is False, cfg["security_notice"])

        owner_tok, owner_id = register(http, "owner")
        phone_tok, phone_id = register(http, "phone")
        stranger_tok, stranger_id = register(http, "stranger")
        user_ids = [owner_id, phone_id, stranger_id]
        oh, ph, sh = ({"Authorization": f"Bearer {t}"} for t in (owner_tok, phone_tok, stranger_tok))

        print("\n1) Owner creates the meeting on the desktop")
        m = http.post(f"{API}/meetings", headers=oh, json={"title": "Multiplatform session check"}).json()
        mid, code = m["id"], m["code"]
        check("meeting created with a code and a QR join link", bool(code) and f"code={code}" in m["join_url"], m["join_url"])
        owner_doc = upload(http, owner_tok, f"lease_{RUN}.txt", LEASE)
        sel = http.put(f"{API}/meetings/{mid}/documents", headers=oh, json={"document_ids": [owner_doc]})
        check("owner selects the meeting documents", sel.status_code == 200 and sum(d["selected"] for d in sel.json()["documents"]) == 1)
        desktop = Device("desktop", mid, owner_tok)
        devices.append(desktop)

        print("2) Stranger who never joined is blocked")
        check("stranger cannot open the meeting (403)", http.get(f"{API}/meetings/{mid}", headers=sh).status_code == 403)
        try:
            Device("stranger", mid, stranger_tok)
            blocked = False
        except Exception:
            blocked = True
        check("stranger's live connection is refused", blocked)

        print("3) Second account scans the QR / enters the code and joins the SAME session")
        j = http.post(f"{API}/meetings/join", headers=ph, json={"code": code})
        check("join by code works and returns the same meeting", j.status_code == 200 and j.json()["id"] == mid)
        phone = Device("phone", mid, phone_tok)
        devices.append(phone)
        check("phone gets the live snapshot of the same meeting", phone.snapshot["data"]["meeting"]["id"] == mid)
        check("desktop sees the second device connect (presence)", desktop.has_event("session.presence"))

        print("4) Question typed on the PHONE: answered from the OWNER's documents, shown on both")
        phone_doc = upload(http, phone_tok, f"notes_{RUN}.txt", PARTICIPANT_DOC)  # phone account has its own private doc
        a1 = phone.ask("What is the monthly rent in the lease and what is the security deposit?")
        t_phone, kind = phone.answer(a1)
        t_desk, _ = desktop.answer(a1)
        check("answer uses the owner's lease (32,500 and 97,500)", kind == "answer.completed" and "32,500" in t_phone and "97,500" in t_phone, t_phone)
        check("desktop received the identical answer", t_desk == t_phone)

        print("5) The phone account's PRIVATE document is not used in the owner's session")
        a2 = phone.ask("What is the secret project codename?")
        t2, _ = phone.answer(a2)
        check("no leak of the second account's private notes", "ORCHID" not in t2.upper() and "7731" not in t2, t2)

        print("6) Follow-up on the DESKTOP with session memory, phone sees it too")
        a3 = desktop.ask("And how much notice is needed to end the lease?")
        t3, _ = desktop.answer(a3)
        t3p, _ = phone.answer(a3)
        check("60 days, same text on both devices", "60" in t3 and t3 == t3p, t3)

        print("7) Screen analysis from the desktop appears on the phone; phone can trigger one too")
        png = screenshot(["Question 3 of 5", "", "Which HTTP method is idempotent?", "  A) POST   B) PUT   C) PATCH   D) CONNECT"])
        r = http.post(f"{API}/meetings/{mid}/screen-ask", headers=oh, files={"image": ("s.png", png, "image/png")}, data={"note": ""})
        a4 = r.json()["answer_id"]
        t4, _ = desktop.answer(a4)
        t4p, _ = phone.answer(a4)
        check("screen answer picks PUT on both devices", "PUT" in t4 and t4 == t4p, t4)
        r = http.post(f"{API}/meetings/{mid}/screen-ask", headers=ph, files={"image": ("s.png", png, "image/png")}, data={"note": "answer briefly"})
        a5 = r.json()["answer_id"]
        t5, _ = desktop.answer(a5)
        check("a participant's screen request reaches the desktop too", "PUT" in t5)

        print("8) Roles: only the owner controls the meeting")
        check("participant cannot change the meeting documents (403)",
              http.put(f"{API}/meetings/{mid}/documents", headers=ph, json={"document_ids": [phone_doc]}).status_code == 403)
        view = http.get(f"{API}/meetings/{mid}/documents", headers=ph).json()
        check("participant only sees which documents the session uses", [d["filename"] for d in view["documents"]] == [f"lease_{RUN}.txt"])
        check("participant cannot end the meeting (403)", http.post(f"{API}/meetings/{mid}/end", headers=ph).status_code == 403)

        print("9) A device that reconnects gets the full history")
        phone.close()
        phone2 = Device("phone-reconnected", mid, phone_tok)
        devices.append(phone2)
        texts = {a["id"]: a["answer_text"] for a in phone2.snapshot["data"]["answers"]}
        check("reconnected phone has all earlier answers (incl. screen answers)",
              all(x in texts for x in (a1, a3, a4, a5)) and texts[a3] == t3)

        print("10) Owner ends the meeting: every device is told")
        http.post(f"{API}/meetings/{mid}/end", headers=oh)
        check("phone receives meeting.ended", phone2.has_event("meeting.ended"))
        check("desktop receives meeting.ended", desktop.has_event("meeting.ended"))
    finally:
        for d in devices:
            d.close()
        # remove the test accounts (cascades to their workspace, meetings, documents)
        try:
            sys.path.insert(0, str(BACKEND))
            import asyncio
            from sqlalchemy import delete
            from app.core.database import SessionLocal
            from app.models import User

            async def cleanup():
                async with SessionLocal() as db:
                    await db.execute(delete(User).where(User.email.like(f"ma-test-{RUN}-%")))
                    await db.commit()
            asyncio.run(cleanup())
        except Exception as exc:  # noqa: BLE001
            print("cleanup note:", exc)
        subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], capture_output=True)
        print(f"\n{sum(results)}/{len(results)} passed")


if __name__ == "__main__":
    main()

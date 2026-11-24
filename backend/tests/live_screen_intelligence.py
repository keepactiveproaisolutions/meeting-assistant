"""Live test of AI Screen Intelligence through the REAL running backend (HTTP + WebSocket) with the real vision model.
Screenshots are rendered synthetically with Pillow (nothing from your real screen is sent).
Needs the backend on 127.0.0.1:8000 and httpx + websocket-client + Pillow (desktop-app venv has all three):
    desktop-app\\.venv\\Scripts\\python.exe backend\\tests\\live_screen_intelligence.py"""
import io
import json
import re
import threading
import time

import httpx
import websocket
from PIL import Image, ImageDraw, ImageFont

API = "http://127.0.0.1:8000/api/v1"
WS = "ws://127.0.0.1:8000"
H = {"Authorization": "Bearer dev"}
results: list[bool] = []


def check(name, ok, text):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n     ANSWER: {text[:380].replace(chr(10), ' ')}\n")


def font(size):
    for f in ("consola.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def screenshot(lines, title, dark=False, size=(1500, 820)):
    bg, fg = ((30, 30, 30), (230, 230, 230)) if dark else ((255, 255, 255), (20, 20, 20))
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, size[0], 44], fill=(60, 60, 70))
    d.text((16, 10), title, fill=(255, 255, 255), font=font(22))
    y = 70
    for line in lines:
        d.text((40, y), line, fill=fg, font=font(26))
        y += 40
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


CODING = screenshot([
    "Problem 1: Two Sum",
    "",
    "Given an array of integers nums and an integer target, return the",
    "indices of the two numbers such that they add up to target.",
    "You may assume exactly one solution exists, and you may not use",
    "the same element twice. You can return the answer in any order.",
    "",
    "Example: nums = [2, 7, 11, 15], target = 9  ->  Output: [0, 1]",
    "Constraints: 2 <= nums.length <= 10^4",
    "",
    "def twoSum(self, nums: List[int], target: int) -> List[int]:",
], "LeetCode - Two Sum (Python3)")

MCQ = screenshot([
    "Question 4 of 10",
    "",
    "Which HTTP status code means the resource was created successfully?",
    "",
    "  A)  200 OK",
    "  B)  201 Created",
    "  C)  204 No Content",
    "  D)  301 Moved Permanently",
], "Online Assessment - Backend Fundamentals")

ERROR = screenshot([
    "Traceback (most recent call last):",
    '  File "app.py", line 12, in <module>',
    "    total = sum(prices) / len(prices)",
    "ZeroDivisionError: division by zero",
    "",
    "prices = []   # loaded from the empty CSV file",
], "Terminal - python app.py", dark=True)


class Client:
    def __init__(self, meeting_id):
        self.events, self.lock = [], threading.Lock()
        self.ws = websocket.create_connection(f"{WS}/ws/meetings/{meeting_id}?token=dev", timeout=150)
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

    def wait(self, aid, timeout=150):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                ev = list(self.events)
            done = next((m for m in ev if m["type"] in ("answer.completed", "answer.failed") and m["data"]["answer_id"] == aid), None)
            if done:
                started = next((m for m in ev if m["type"] == "answer.started" and m["data"]["answer_id"] == aid), None)
                text = "".join(m["data"]["delta"] for m in ev if m["type"] == "answer.delta" and m["data"]["answer_id"] == aid)
                return done, text, started
            time.sleep(0.1)
        raise TimeoutError(aid)

    def ask_text(self, q):
        self.ws.send(json.dumps({"type": "ask", "data": {"question": q}}))
        end = time.monotonic() + 30
        while time.monotonic() < end:
            with self.lock:
                s = next((m for m in self.events if m["type"] == "answer.started" and m["data"].get("question_text") == q), None)
            if s:
                return self.wait(s["data"]["answer_id"])
            time.sleep(0.1)
        raise TimeoutError(q)


def screen_ask(http, meeting_id, png, note=""):
    r = http.post(f"{API}/meetings/{meeting_id}/screen-ask", headers=H, files={"image": ("screen.png", png, "image/png")},
                  data={"note": note})
    return r


def main():
    http = httpx.Client(timeout=90)
    m = http.post(f"{API}/meetings", headers=H, json={"title": "Screen intelligence check (safe to ignore)"}).json()
    mid = m["id"]
    c = Client(mid)
    phone = Client(mid)  # a second device (phone browser / web app) in the same meeting
    try:
        print("1) Coding problem screenshot -> understand + solution")
        r = screen_ask(http, mid, CODING)
        assert r.status_code == 202, r.text
        done, text, started = c.wait(r.json()["answer_id"])
        check("answer is tagged [Screen]", started and started["data"]["question_text"].startswith("[Screen]"), str(started and started["data"]["question_text"]))
        check("identifies Two Sum, gives runnable code + complexity",
              done["type"] == "answer.completed" and "def twoSum" in text and "```" in text and bool(re.search(r"O\(n\)", text))
              and bool(re.search(r"(?i)hash|dict|map", text)), text)
        check("code block extracted for the UI", len(done["data"].get("code_blocks", [])) >= 1, str(done["data"].get("code_blocks"))[:200])

        print("2) Follow-up in text (memory of the screen problem)")
        done, text, _ = c.ask_text("Now give me the same solution in Java.")
        check("Java version of Two Sum from memory, no clarify",
              "clarify" not in text.lower() and bool(re.search(r"(?i)hashmap|int\[\]", text)), text)

        print("3) Multiple-choice question screenshot")
        r = screen_ask(http, mid, MCQ)
        done, text, _ = c.wait(r.json()["answer_id"])
        check("picks B 201 Created", bool(re.search(r"(?i)\bB\b|201", text)) and "201" in text, text)

        print("4) Error screenshot -> explain + fix")
        r = screen_ask(http, mid, ERROR)
        done, text, _ = c.wait(r.json()["answer_id"])
        check("explains empty list division and gives a guard",
              bool(re.search(r"(?i)empty|zero", text)) and bool(re.search(r"(?i)if (not )?prices|len\(prices\)", text)), text)

        print("5) Instruction in the note (language)")
        r = screen_ask(http, mid, CODING, note="solve it in JavaScript")
        done, text, started = c.wait(r.json()["answer_id"])
        check("note appears in the question and JavaScript code is returned",
              "JavaScript" in started["data"]["question_text"] and bool(re.search(r"(?i)```(javascript|js)|function|=>|new Map", text)), text)

        print("6) Sync: phone/web client sees exactly what the desktop sees")
        aid = r.json()["answer_id"]  # the JavaScript screen answer
        d_done, d_text, d_start = c.wait(aid)
        p_done, p_text, p_start = phone.wait(aid)
        check("phone received the same streamed text as desktop", p_text == d_text and len(p_text) > 50, p_text)
        check("phone received the same final answer + code blocks",
              p_done["data"]["answer_text"] == d_done["data"]["answer_text"]
              and p_done["data"].get("code_blocks") == d_done["data"].get("code_blocks")
              and len(p_done["data"].get("code_blocks", [])) >= 1, str(p_done["data"].get("code_blocks"))[:160])
        check("phone sees the same question label", p_start["data"]["question_text"] == d_start["data"]["question_text"], p_start["data"]["question_text"])
        late = Client(mid)  # a device that joins afterwards (or reconnects)
        snap_answers = {a["id"]: a for a in late.snapshot["data"]["answers"]}
        late.ws.close()
        check("late joiner's snapshot contains the screen answer with full text",
              aid in snap_answers and snap_answers[aid]["answer_text"] == d_done["data"]["answer_text"]
              and snap_answers[aid]["question_text"].startswith("[Screen]"), str(snap_answers.get(aid, {}).get("question_text")))
        phone_text_q = phone.ask_text("Why a hash map there?")
        desk_done, desk_text, _ = c.wait(phone_text_q[2]["data"]["answer_id"])
        check("a question typed on the phone shows up on the desktop with the same answer",
              desk_text == phone_text_q[1] and "clarify" not in desk_text.lower(), desk_text)

        print("7) Bad input is rejected")
        r = http.post(f"{API}/meetings/{mid}/screen-ask", headers=H, files={"image": ("x.png", b"not an image", "image/png")})
        check("non-image -> 415", r.status_code == 415, r.text)
        r = http.post(f"{API}/meetings/{mid}/screen-ask", headers=H, files={"image": ("x.png", b"", "image/png")})
        check("empty file -> 413/422", r.status_code in (413, 422), r.text)
    finally:
        for cl in (c, phone):
            try:
                cl.ws.close()
            except Exception:
                pass
        http.post(f"{API}/meetings/{mid}/end", headers=H)
        print(f"\n{sum(results)}/{len(results)} passed")


if __name__ == "__main__":
    main()

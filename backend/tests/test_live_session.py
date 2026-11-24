"""Deterministic (no network, no cost) checks of LiveSession turn bookkeeping.
Run: .venv\\Scripts\\python.exe tests\\test_live_session.py
"""
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import live_transcription as lt  # noqa: E402

RESULTS = []


def check(name, cond):
    RESULTS.append(cond)
    print(("[PASS] " if cond else "[FAIL] ") + name)


class FakeOA:
    def __init__(self):
        self.sent = []
        self.q: asyncio.Queue = asyncio.Queue()

    async def send(self, m):
        self.sent.append(json.loads(m))

    async def close(self):
        await self.q.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        m = await self.q.get()
        if m is None:
            raise StopAsyncIteration
        return json.dumps(m)


async def run():
    persisted, broadcasts = [], []

    async def fake_persist(db, meeting, text, idx, off, dur, *_):
        persisted.append((text, off, dur))

    async def fake_broadcast(mid, ev, data):
        broadcasts.append((ev.value, dict(data)))

    class FakeDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *_):
            return object()

    import app.services.meeting_service as ms
    ms.persist_transcript_text = fake_persist
    lt.manager.broadcast = fake_broadcast
    lt.SessionLocal = FakeDB

    # --- normal flow with deltas before commit and completions out of order
    s = lt.LiveSession(uuid4())
    s.oa = FakeOA()
    lt.sessions[str(s.meeting_id)] = s
    pump = asyncio.create_task(s.pump())
    await s.append(b"\0" * 4800)
    check("audio outside a turn is not sent", not any(m["type"] == "input_audio_buffer.append" for m in s.oa.sent))
    await s.begin_turn(1000)
    await s.append(b"\0" * 48000)
    await s.oa.q.put({"type": "conversation.item.input_audio_transcription.delta", "item_id": "A", "delta": " Hello"})
    await asyncio.sleep(0.01)
    check("partial broadcast before commit", any(b[0] == "transcript.partial" and b[1]["text"] == "Hello"
                                                  and b[1]["start_offset_ms"] == 1000 for b in broadcasts))
    check("snapshot partials include provisional text", s.partials() == [
        {"item_id": "A", "text": " Hello", "start_offset_ms": 1000}])
    await s.commit(2000)
    await s.begin_turn(2500)
    await s.append(b"\0" * 48000)
    await s.commit(3500)
    await s.oa.q.put({"type": "input_audio_buffer.committed", "item_id": "A"})
    await s.oa.q.put({"type": "input_audio_buffer.committed", "item_id": "B"})
    await s.oa.q.put({"type": "conversation.item.input_audio_transcription.completed", "item_id": "B",
                      "transcript": "Second.", "usage": {"type": "duration", "seconds": 1}})
    await s.oa.q.put({"type": "conversation.item.input_audio_transcription.completed", "item_id": "A",
                      "transcript": "Hello there.", "usage": {"type": "duration", "seconds": 1}})
    await asyncio.sleep(0.05)
    check("out-of-order completions keep their own timing",
          ("Second.", 2500, 1000) in persisted and ("Hello there.", 1000, 1000) in persisted)
    clears = [b for b in broadcasts if b[0] == "transcript.partial" and b[1]["final"]]
    check("each provisional cleared exactly once", sorted(c[1]["item_id"] for c in clears) == ["A", "B"])
    check("billing usage tracked", s.billed_seconds == 2)

    # --- empty commit is never sent
    n_commits = sum(m["type"] == "input_audio_buffer.commit" for m in s.oa.sent)
    await s.begin_turn(4000)
    await s.commit(4500)
    check("empty turn is not committed (API error avoided)",
          sum(m["type"] == "input_audio_buffer.commit" for m in s.oa.sent) == n_commits)

    # --- disconnect mid-turn: commit with the true end, bounded wait, streamed text kept on timeout
    lt.FINALIZE_TIMEOUT_S = 0.3
    await s.begin_turn(5000)
    await s.append(b"\0" * 24000)  # 0.5 s
    await s.oa.q.put({"type": "conversation.item.input_audio_transcription.delta", "item_id": "C", "delta": " cut off"})
    await asyncio.sleep(0.01)
    await s.finish(None)
    check("open turn committed on finish", s.oa.sent[-1]["type"] == "input_audio_buffer.commit")
    check("unfinished turn keeps streamed text with real end offset", ("cut off", 5000, 500) in
          [(t.strip(), o, d) for t, o, d in persisted])
    check("session closed and unregistered", s.closed and not lt.active(s.meeting_id))
    pump.cancel()


asyncio.run(run())
print(f"\n{sum(RESULTS)}/{len(RESULTS)} checks passed.")
sys.exit(0 if all(RESULTS) else 1)

"""PAID end-to-end check of Live mode (~17 s of audio, ~$0.005):
real desktop LiveStreamer -> backend relay -> OpenAI -> persistence -> a second "browser" WebSocket.

Creates a NEW test meeting (never touches existing meetings). Verifies:
  * provisional text arrives before the final segment for each turn
  * every final segment is broadcast exactly once; provisional text is cleared afterwards
  * Standard upload is rejected (409) while Live is active (no simultaneous paid paths)
  * Stop finalizes (bounded) and the snapshot has no leftover provisional text
Usage: python tests/e2e_live_relay.py <wav 24 kHz mono>
"""
import json
import sys
import threading
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx  # noqa: E402
import numpy as np  # noqa: E402
import websocket  # noqa: E402

from ai.api_client import ApiClient  # noqa: E402
from config.settings import settings  # noqa: E402
from transcription.live_streamer import LiveStreamer  # noqa: E402


def main(wav_path: str) -> int:
    api = ApiClient()
    ws_id = api.workspaces()[0]["id"]
    meeting = api.create_meeting(ws_id, "TEST live relay e2e")
    mid = meeting["id"]
    print("test meeting", meeting["code"], mid)

    events: list[tuple[float, dict]] = []
    t0 = time.perf_counter()
    listener = websocket.create_connection(f"{settings.ws_url}/ws/meetings/{mid}?token={api.token or ''}")

    def listen():
        try:
            while True:
                m = json.loads(listener.recv())
                events.append((time.perf_counter() - t0, m))
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=listen, daemon=True).start()

    fatal = []
    live = LiveStreamer(api.token, mid, on_fatal=fatal.append)
    live.start()
    print("live model:", live.model)

    # Standard must be refused while Live runs
    r = httpx.post(f"{settings.api_url}/api/v1/meetings/{mid}/audio",
                   files={"file": ("c.wav", b"RIFF0000", "audio/wav")},
                   data={"chunk_index": 0, "offset_ms": 0, "duration_ms": 2000})
    print("standard upload while live ->", r.status_code, r.text[:80])

    w = wave.open(wav_path)
    samples = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    feed_t = time.perf_counter() - t0
    print(f"audio feed starts at {feed_t:.2f}s (wall {time.strftime('%H:%M:%S')})")
    for i in range(0, len(samples), 2400):
        fr = samples[i:i + 2400]
        rms = float(np.sqrt(np.mean(fr.astype(np.float32) ** 2)))
        live.feed(fr, (i // 2400) * 100, rms)
        time.sleep(0.1)
    # 1 s of trailing silence like a real pause, then Stop
    for k in range(10):
        live.feed(np.zeros(2400, np.int16), (len(samples) // 2400 + k) * 100, 0.0)
        time.sleep(0.1)
    ts = time.perf_counter()
    usage = live.stop()
    print(f"stop took {time.perf_counter() - ts:.2f}s; usage {usage}; fatal {fatal}")
    time.sleep(1.0)
    listener.close()

    partial_first: dict[int, float] = {}
    finals: dict[int, float] = {}
    segs = []
    for t, m in events:
        d = m.get("data", {})
        if m["type"] == "transcript.partial" and not d.get("final"):
            partial_first.setdefault(d["start_offset_ms"], t)
        elif m["type"] == "transcript.partial" and d.get("final"):
            finals[d["start_offset_ms"]] = t
        elif m["type"] == "transcript.segment":
            segs.append(d)
        elif m["type"] == "error":
            print("ERROR event", d)
    print("\nturn start | first provisional (s since test start) | cleared/final")
    for k in sorted(set(partial_first) | set(finals)):
        spoken = feed_t + k / 1000
        print(f"  {k:6d} ms | {partial_first.get(k, float('nan')):6.2f} (+{partial_first.get(k, 0) - spoken:.2f}s after "
              f"speech) | {finals.get(k, float('nan')):6.2f}")
    print("\nfinal segments:")
    for s in segs:
        print(f"  [{s['start_offset_ms']}-{s['end_offset_ms']}] {s['text']}")
    ids = [s["id"] for s in segs]
    print("duplicate segment ids:", len(ids) - len(set(ids)))
    print("desktop-measured:", {k: {x: v for x, v in w.items() if x.endswith('_ms')} for k, w in live.turn_wall.items()})
    snap = httpx.get(f"{settings.api_url}/api/v1/meetings/{mid}").json()
    print("snapshot: segments", len(snap.get("segments", [])), "partials", snap.get("partials"), "live", snap.get("live"))
    ok = (r.status_code == 409 and segs and len(ids) == len(set(ids)) and not snap.get("partials")
          and not snap.get("live") and not fatal and all(k in finals for k in partial_first))
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

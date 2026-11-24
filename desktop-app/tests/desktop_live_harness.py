"""PAID test through the real desktop app code (MainWindow + Controller + WASAPI loopback + Live mode).

Plays a speech WAV through the Windows DEFAULT playback device (system audio only; the microphone is never opened),
while a separate "browser" WebSocket listens to the same meeting. Uses a NEW test meeting.
Scenario A: Live end to end, then Stop (bounded finalization, paid stream closed).
Scenario B: Live, then a simulated Live failure mid-speech -> Live closed, Standard continues (never both at once).
The user's saved device / mode settings are restored afterwards.
Usage: python tests/desktop_live_harness.py <wav>
"""
import json
import logging
import sys
import threading
import time
import winsound
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import websocket  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from ai.api_client import ApiClient  # noqa: E402
from config.settings import settings  # noqa: E402
from main.app import Controller  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d %(name)s %(message)s", datefmt="%H:%M:%S")
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("harness")


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def listener(mid, token, sink):
    ws = websocket.create_connection(f"{settings.ws_url}/ws/meetings/{mid}?token={token or ''}")

    def run():
        try:
            while True:
                sink.append((time.perf_counter(), json.loads(ws.recv())))
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=run, daemon=True).start()
    return ws


def summarize(name, events, t_play):
    parts, segs, finals = {}, [], 0
    for t, m in events:
        d = m.get("data", {})
        if m["type"] == "transcript.partial":
            if d.get("final"):
                finals += 1
            else:
                parts.setdefault(d["start_offset_ms"], t - t_play)
        elif m["type"] == "transcript.segment":
            segs.append(d)
    ids = [s["id"] for s in segs]
    print(f"\n--- {name}: browser-side")
    print("  first provisional per turn (s after playback start):", {k: round(v, 2) for k, v in sorted(parts.items())})
    for s in segs:
        print(f"  [{s['start_offset_ms']}-{s['end_offset_ms']}] {s['text']}")
    print("  segments", len(segs), "duplicates", len(ids) - len(set(ids)), "partial clears", finals)
    return segs, ids


def main(wav):
    app = QApplication.instance() or QApplication(sys.argv)
    QMessageBox.warning = staticmethod(lambda *a, **k: log.warning("MessageBox: %s", a[1:]))  # never block
    api = ApiClient()
    win = MainWindow(dev_mode=True)
    saved_dev = win.settings.value("audio/endpoint_id")
    saved_mode = win.settings.value("transcription/mode")
    ctl = Controller(api, win, api.workspaces()[0]["id"])
    win.show()
    ok = True
    try:
        # ---------------- Scenario A
        ctl.create_and_start("TEST desktop live A")
        mid = ctl.meeting["id"]
        log.info("meeting A %s", ctl.meeting["code"])
        ev = []
        lw = listener(mid, api.token, ev)
        pump(app, 1.5)
        win.set_mode("live")
        win.device_combo.setCurrentIndex(max(0, win.device_combo.findData("default")))
        assert not ctl.is_capturing, "capture must not auto-start"
        ctl.start_capture()
        log.info("mode=%s live=%s capture=%s", ctl.capture_mode, bool(ctl.live), ctl.capture and ctl.capture.device_name)
        pump(app, 0.5)
        t_play = time.perf_counter()
        winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
        pump(app, 19.5)
        t_stop = time.perf_counter()
        ctl.stop_capture(user_initiated=True)
        log.info("Stop returned in %.2f s; live=%s capture=%s", time.perf_counter() - t_stop, ctl.live, ctl.capture)
        pump(app, 1.5)
        lw.close()
        segs, ids = summarize("A (Live)", ev, t_play)
        prov_left = win.transcript.provisional.isVisible()
        print("  desktop provisional still visible after stop:", prov_left)
        ok &= bool(segs) and len(ids) == len(set(ids)) and not prov_left and ctl.live is None

        # ---------------- Scenario B: simulated Live failure -> Standard
        ctl.create_and_start("TEST desktop live B")
        mid = ctl.meeting["id"]
        log.info("meeting B %s", ctl.meeting["code"])
        ev = []
        lw = listener(mid, api.token, ev)
        pump(app, 1.5)
        ctl.start_capture()
        pump(app, 0.5)
        t_play = time.perf_counter()
        winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
        pump(app, 6.0)
        log.info("simulating Live failure")
        ctl.bridge.live_failed.emit("simulated failure")
        pump(app, 0.3)
        log.info("after fallback: mode=%s live=%s uploader=%s", ctl.capture_mode, ctl.live, bool(ctl.uploader))
        ok &= ctl.capture_mode == "standard" and ctl.live is None and ctl.uploader is not None
        pump(app, 14.0)
        ctl.stop_capture(user_initiated=True)
        pump(app, 1.5)
        lw.close()
        segs, ids = summarize("B (Live -> Standard fallback)", ev, t_play)
        ok &= bool(segs) and len(ids) == len(set(ids))

        # ---------------- Scenario C: Stop while Live is still connecting -> no orphaned paid stream
        import httpx
        ctl.create_and_start("TEST desktop live C")
        mid = ctl.meeting["id"]
        win.set_mode("live")
        ctl.start_capture()
        pump(app, 0.2)
        ctl.stop_capture(user_initiated=True)
        pump(app, 8.0)
        snap = httpx.get(f"{settings.api_url}/api/v1/meetings/{mid}").json()
        print(f"\n--- C (stop while connecting): backend live session active afterwards = {snap.get('live')}")
        ok &= not snap.get("live")
    finally:
        winsound.PlaySound(None, 0)
        ctl.stop()
        if saved_dev is not None:
            win.settings.setValue("audio/endpoint_id", saved_dev)
        if saved_mode is not None:
            win.settings.setValue("transcription/mode", saved_mode)
        win.close()
    print("\nHARNESS RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

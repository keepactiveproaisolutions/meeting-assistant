"""PAID accuracy + latency test through the real desktop app (Live mode, playback loopback only).
Plays <wav> on the default output, a browser-like listener records events, then computes word error rate (WER)
against <reference.txt>. New test meeting; saved device/mode settings restored.
Usage: python tests/desktop_live_accuracy.py <wav> <reference.txt> [seconds]
"""
import re
import sys
import time
import winsound
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from ai.api_client import ApiClient  # noqa: E402
from desktop_live_harness import listener, log, pump  # noqa: E402
from main.app import Controller  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from config.settings import settings  # noqa: E402
import os  # noqa: E402
import json  # noqa: E402


def words(t):
    t = t.lower().replace("check sums", "checksums")
    return re.findall(r"[a-z0-9]+", t)


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(1, len(r)), len(r)


def analyze(probes):
    print("--- browser checks (web + phone)")
    bad = []
    prev = {}
    for t, name, p in probes:
        if not p:
            bad.append(f"{t}s {name}: probe failed")
            continue
        if p["bottomStrip"]:
            bad.append(f"{t}s {name}: separate bottom strip present")
        for tail in p["tails"]:
            if any(tail and tail in f for f in p["finals"]):
                bad.append(f"{t}s {name}: provisional text duplicated in finalized text: {tail[:40]}")
        for i, (f, dis) in enumerate(zip(p["finals"], p["sendDisabled"])):
            if not f and not dis:
                bad.append(f"{t}s {name}: card without finalized text has Send enabled")
        key = name.split("-")[0]
        old = prev.get(key)
        if old and name == key:
            for a, b in zip(old["finals"], p["finals"]):
                if a and not b.startswith(a):
                    bad.append(f"{t}s {name}: finalized text changed/jumped: {a[:30]!r} -> {b[:30]!r}")
        prev[key] = p
    tails_seen = sum(1 for _, _, p in probes if p and p["tails"])
    print(f"  probes {len(probes)}, probes showing inline provisional text {tails_seen}")
    last = {n: p for _, n, p in probes if p}
    print("  final cards web/phone/phone-reload:", last["web"]["cards"], last["phone"]["cards"],
          last["phone-reload"]["cards"])
    if last["web"]["finals"] != last["phone"]["finals"] or last["phone"]["finals"] != last["phone-reload"]["finals"]:
        bad.append("web / phone / reloaded phone finalized text differ")
    print("  phone url after QR join:", last["phone"]["url"])
    print("  problems:", bad or "none")


def main(wav, ref_path, seconds):
    app = QApplication.instance() or QApplication(sys.argv)
    QMessageBox.warning = staticmethod(lambda *a, **k: log.warning("MessageBox: %s", a[1:]))
    api = ApiClient()
    win = MainWindow(dev_mode=True)
    saved = {k: win.settings.value(k) for k in ("audio/endpoint_id", "transcription/mode")}
    ctl = Controller(api, win, api.workspaces()[0]["id"])
    import traceback
    desk_asks = []

    def _blocked_ask(*a, **k):
        desk_asks.append("".join(traceback.format_stack(limit=25)))
        log.warning("DESKTOP ASK BLOCKED: %s | stack:\n%s", str(a[1])[:80] if len(a) > 1 else a, desk_asks[-1])
        return {}
    api.ask = _blocked_ask
    win.show()
    try:
        ctl.create_and_start("TEST live accuracy AWS")
        mid = ctl.meeting["id"]
        log.info("meeting %s", ctl.meeting["code"])
        ev = []
        lw = listener(mid, api.token, ev)
        pump(app, 1.5)
        win.set_mode("live")
        win.device_combo.setCurrentIndex(max(0, win.device_combo.findData("default")))
        ctl.start_capture()
        shots = os.getenv("SHOTS")
        br = tabs = None
        if shots:
            from browser_cdp import PROBE_JS, Browser
            Path(shots).mkdir(parents=True, exist_ok=True)
            br = Browser(str(Path(shots) / "edge-profile"))
            web = br.tab(1280, 800)
            web.goto(f"{settings.web_url}/meeting/{mid}")
            phone = br.tab(390, 844, mobile=True)
            phone.goto(ctl.meeting.get("join_url") or f"{settings.web_url}/join?code={ctl.meeting['code']}", 5)
            tabs = {"web": web, "phone": phone}
            log.info("browser tabs: web %s | phone joined via %s -> %s", web.eval("location.href"),
                     ctl.meeting.get("join_url"), phone.eval("location.href"))
        pump(app, 0.3)
        winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
        probes = []
        t0 = time.monotonic()
        marks = [7.5, 19.0, 34.0, 49.0]
        while time.monotonic() - t0 < seconds + 2.5:
            pump(app, 0.9)
            if tabs:
                el = time.monotonic() - t0
                for name, tab in tabs.items():
                    probes.append((round(el, 1), name, tab.eval(PROBE_JS)))
                if marks and el >= marks[0]:
                    m = marks.pop(0)
                    win.grab().save(str(Path(shots) / f"live_{int(m):02d}s_desktop.png"))
                    for name, tab in tabs.items():
                        tab.shot(str(Path(shots) / f"live_{int(m):02d}s_{name}.png"))
        ctl.stop_capture(user_initiated=True)
        pump(app, 2.0)
        lw.close()
        if tabs:
            win.grab().save(str(Path(shots) / "final_desktop.png"))
            for name, tab in tabs.items():
                tab.shot(str(Path(shots) / f"final_{name}.png"))
                probes.append((99, name, tab.eval(PROBE_JS)))
            # reconnect / history load: a fresh page load must show identical history, no live tail
            tabs["phone"].goto(ctl.meeting.get("join_url"), 5)
            probes.append((100, "phone-reload", tabs["phone"].eval(PROBE_JS)))
            for name, tab in tabs.items():
                print(f"--- {name} tab /ask calls:", json.dumps(tab.eval("window.__asks || []"), indent=1)[:3000])
            br.close()
            analyze(probes)
    finally:
        winsound.PlaySound(None, 0)
        ctl.stop()
        for k, v in saved.items():
            if v is not None:
                win.settings.setValue(k, v)
        win.close()

    print(f"--- desktop ask calls: {len(desk_asks)}")
    for st in desk_asks:
        print(st)
    segs = [m["data"] for _, m in ev if m["type"] == "transcript.segment"]
    ids = [s["id"] for s in segs]
    segs.sort(key=lambda s: s["start_offset_ms"])
    hyp = " ".join(s["text"] for s in segs)
    ref = Path(ref_path).read_text(encoding="utf-8")
    rate, n = wer(ref, hyp)
    print("\n--- transcript received by the browser listener:")
    for s in segs:
        print(f"  [{s['start_offset_ms']:>6}-{s['end_offset_ms']:>6}] {s['text']}")
    print(f"\nsegments {len(segs)}, duplicate ids {len(ids) - len(set(ids))}")
    print(f"WER {rate * 100:.1f}% over {n} reference words")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 55))

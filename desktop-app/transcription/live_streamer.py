"""Live mode: stream system-playback audio to the backend Live relay (backend holds the OpenAI key).

Application-side turn detection (the Realtime transcription models take turn_detection=null):
  * a turn starts on the first voiced 100 ms frame (with 200 ms of pre-roll so the first syllable isn't clipped);
  * it is committed after PAUSE_MS of silence, after MAX_TURN_MS, or when frames stop arriving (device paused);
  * audio between turns is never sent, so silence is not billed.
Offsets come from the capture clock (application-level segment timing; no invented word timestamps).
"""
from __future__ import annotations

import json
import os
import logging
import threading
import time
from collections import deque

import numpy as np
import websocket

from config.settings import settings

log = logging.getLogger("live")
tlog = logging.getLogger("timing.live")

# A turn opens only after MIN_VOICED_FRAMES consecutive voiced frames (about 300 ms of sustained sound) above
# VOICE_RMS. A single click, keystroke or hum frame no longer opens a turn: such audio is what makes speech-to-text
# invent text ("Thank you for watching", other scripts). OpenAI VAD guidance: a higher threshold performs better in
# noisy environments. Both are overridable for tuning.
VOICE_RMS = float(os.getenv("MA_LIVE_VOICE_RMS", "100"))
MIN_VOICED_FRAMES = int(os.getenv("MA_LIVE_MIN_VOICED_FRAMES", "3"))
PAUSE_MS = 600
MAX_TURN_MS = 15_000
FRAME_MS = 100
PREROLL_FRAMES = 2
STALL_S = 0.8          # no frames for this long while in a turn -> commit (device paused / capture stopping)
ROLLOVER_S = float(os.getenv("MA_LIVE_ROLLOVER_S", str(50 * 60)))   # Realtime sessions are limited to 60 min: reconnect between turns before that


class LiveError(RuntimeError):
    pass


class LiveStreamer:
    def __init__(self, token: str | None, meeting_id: str, on_fatal=None, delay: str = "low") -> None:
        self._url = (f"{settings.ws_url}/ws/meetings/{meeting_id}/live-audio?token={token or ''}&delay={delay}")
        self._on_fatal = on_fatal or (lambda m: log.error(m))
        self._ws: websocket.WebSocket | None = None
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stopped_evt = threading.Event()
        self._closing = False
        self._fatal_sent = False
        self.model = ""
        self.usage: dict = {}
        self.usage_total = {"audio_sent_s": 0.0, "billed_s": 0.0, "est_usd": 0.0, "finals": 0}
        # turn state
        self._in_turn = False
        self._turn_start = 0
        self._silent_run = 0
        self._last_end = 0
        self._last_frame_at = 0.0
        self._preroll: deque[tuple[np.ndarray, int]] = deque(maxlen=PREROLL_FRAMES + MIN_VOICED_FRAMES)
        self._voiced_run = 0
        self._opened_at = 0.0
        # timing: turn start offset -> wall clock of first voiced frame / commit
        self.turn_wall: dict[int, dict] = {}
        self._watchdog = threading.Thread(target=self._watch, name="live-watchdog", daemon=True)
        # Frames captured while the stream is still connecting (bounded: 30 s), replayed in order once ready.
        self._ready = False
        self._backlog: deque[tuple[np.ndarray, int, float]] = deque(maxlen=300)

    # ------------------------------------------------------------------ connection
    def start(self) -> None:
        """Non-blocking: capture can start immediately; audio is buffered until the relay is ready."""
        threading.Thread(target=self._start_bg, name="live-connect", daemon=True).start()

    def _start_bg(self) -> None:
        try:
            self._connect()
        except Exception as exc:  # noqa: BLE001
            self._fatal(f"Live unavailable: {exc}")
            return
        with self._state_lock:
            if self._closing:  # Stop/abort happened while connecting: close the just-opened stream (no orphan)
                self.abort()
                return
            backlog = list(self._backlog)
            self._backlog.clear()
            for fr, off, rms in backlog:
                self._process(fr, off, rms)
            self._ready = True
        if backlog:
            log.info("Live: replayed %d buffered frame(s) captured while connecting", len(backlog))
        self._watchdog.start()

    @property
    def ready(self) -> bool:
        return self._ready

    def _connect(self) -> None:
        ws = websocket.create_connection(self._url, timeout=15)
        first = json.loads(ws.recv())
        if first.get("type") != "ready":
            ws.close()
            raise LiveError(first.get("message", "Live transcription unavailable"))
        ws.settimeout(None)
        self.model = first.get("model", "")
        self._ws = ws
        self._stopped_evt = threading.Event()   # one per connection (rollover-safe)
        self._opened_at = time.monotonic()
        threading.Thread(target=self._read, args=(ws, self._stopped_evt), name="live-reader", daemon=True).start()
        log.info("Live stream open (%s, delay=%s)", self.model, first.get("delay"))

    def _read(self, ws: websocket.WebSocket, stopped: threading.Event) -> None:
        try:
            while True:
                raw = ws.recv()
                if not raw:
                    break
                msg = json.loads(raw)
                if msg.get("type") == "stopped":
                    self.usage = msg.get("usage", {})
                    for k in self.usage_total:
                        self.usage_total[k] = round(self.usage_total[k] + float(self.usage.get(k, 0) or 0), 5)
                    stopped.set()
                elif msg.get("type") == "error":
                    log.warning("Live relay error: %s", msg.get("message"))
                    if msg.get("fatal"):
                        self._fatal(msg.get("message", "Live transcription stopped"))
        except Exception as exc:  # noqa: BLE001 - socket closed
            if ws is self._ws and not self._closing:
                self._fatal(f"Live connection lost: {exc}")
        finally:
            stopped.set()

    def _fatal(self, message: str) -> None:
        if self._closing or self._fatal_sent:
            return
        self._fatal_sent = True
        self._on_fatal(message)

    def _send_json(self, obj: dict) -> None:
        with self._send_lock:
            if self._ws is not None:
                self._ws.send(json.dumps(obj))

    def _send_pcm(self, samples: np.ndarray) -> None:
        with self._send_lock:
            if self._ws is not None:
                self._ws.send_binary(samples.astype(np.int16).tobytes())

    # ------------------------------------------------------------------ audio in
    def feed(self, samples: np.ndarray, offset_ms: int, rms: float) -> None:
        """Called from the capture thread for every 100 ms frame (24 kHz mono int16)."""
        if self._closing or self._fatal_sent:
            return
        try:
            with self._state_lock:
                if not self._ready:
                    self._backlog.append((samples, offset_ms, rms))
                    return
                self._process(samples, offset_ms, rms)
        except Exception as exc:  # noqa: BLE001
            self._fatal(f"Live send failed: {exc}")

    def _process(self, samples: np.ndarray, offset_ms: int, rms: float) -> None:
        """Turn detection + sending; caller holds _state_lock."""
        self._last_frame_at = time.monotonic()
        end = offset_ms + FRAME_MS
        voiced = rms >= VOICE_RMS
        if not self._in_turn:
            self._preroll.append((samples, offset_ms))
            if not voiced:
                self._voiced_run = 0
                self._last_end = end
                return
            self._voiced_run += 1
            if self._voiced_run < MIN_VOICED_FRAMES:   # not sustained yet: keep buffering, send nothing
                self._last_end = end
                return
            self._voiced_run = 0
            start = self._preroll[0][1] if self._preroll else offset_ms
            self._in_turn, self._turn_start, self._silent_run = True, start, 0
            self.turn_wall[start] = {"voice_at": time.perf_counter()}
            self._send_json({"type": "turn", "offset_ms": start})
            for s, _ in self._preroll:   # includes the current frame and the buffered pre-roll
                self._send_pcm(s)
            self._preroll.clear()
        else:
            self._send_pcm(samples)
            self._silent_run = 0 if voiced else self._silent_run + FRAME_MS
        self._last_end = end
        if self._silent_run >= PAUSE_MS or end - self._turn_start >= MAX_TURN_MS:
            self._commit_locked(end)

    def _commit_locked(self, end_ms: int) -> None:
        if not self._in_turn:
            return
        self._in_turn = False
        self._silent_run = 0
        self._send_json({"type": "commit", "end_offset_ms": end_ms})
        w = self.turn_wall.get(self._turn_start)
        if w is not None:
            w["commit_at"] = time.perf_counter()
            w["audio_ms"] = end_ms - self._turn_start

    def _watch(self) -> None:
        while not self._closing:
            time.sleep(0.2)
            try:
                rollover = False
                with self._state_lock:
                    if self._in_turn and time.monotonic() - self._last_frame_at > STALL_S:
                        self._commit_locked(self._last_end)
                    age = time.monotonic() - self._opened_at
                    # Realtime sessions end at 60 min: reconnect between turns after ROLLOVER_S (or force it
                    # 5 min later during non-stop speech). Frames are buffered meanwhile - capture never blocks.
                    if self._ready and not self._fatal_sent and age > ROLLOVER_S and (
                            not self._in_turn or age > ROLLOVER_S + 300):
                        self._commit_locked(self._last_end)
                        self._ready = False
                        rollover = True
                if rollover:
                    self._rollover()
            except Exception as exc:  # noqa: BLE001
                self._fatal(f"Live watchdog: {exc}")

    def _rollover(self) -> None:
        t0 = time.perf_counter()
        log.info("Live: rolling over the Realtime session (age %.0f s)", time.monotonic() - self._opened_at)
        self._finish_ws(self._last_end)           # old stream finalizes its turns, then closes
        if self._closing:
            return
        self._connect()
        with self._state_lock:
            backlog = list(self._backlog)
            self._backlog.clear()
            for fr, off, rms in backlog:
                self._process(fr, off, rms)
            self._ready = True
        log.info("Live: rollover done in %d ms, replayed %d buffered frame(s)",
                 (time.perf_counter() - t0) * 1000, len(backlog))

    # ------------------------------------------------------------------ stop
    def _finish_ws(self, end_ms: int) -> None:
        old = self._ws
        if old is None:
            return
        try:
            self._send_json({"type": "stop", "end_offset_ms": end_ms})
            self._stopped_evt.wait(timeout=10)   # backend waits <= 6 s for outstanding finals
        except Exception:  # noqa: BLE001
            pass
        finally:
            with self._send_lock:
                self._ws = None
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass

    def abort(self) -> None:
        """Failure path: drop the connection now (no waiting). The backend still finalizes turns already sent."""
        self._closing = True
        with self._send_lock:
            ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> dict:
        """Commit the open turn, let the backend finalize (bounded), close the paid stream."""
        with self._state_lock:
            self._closing = True
            if not self._ready:   # never connected: nothing was sent or billed
                self._backlog.clear()
                return self.usage_total
            if self._in_turn:
                self._commit_locked(self._last_end)
            self._finish_ws(self._last_end)
        tlog.info("Live usage this capture: %s", self.usage_total)
        return self.usage_total

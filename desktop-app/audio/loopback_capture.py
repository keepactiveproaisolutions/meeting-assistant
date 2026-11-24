"""WASAPI loopback capture of SYSTEM audio only (what the selected speakers/headphones play).

Device selection uses stable Windows endpoint IDs (see audio/endpoints.py), not PortAudio indexes, which shift when
devices connect or disconnect. Two modes:

* DEFAULT_DEVICE ("default"): follow the Windows default playback device. A watchdog notices when the default
  changes (e.g. speakers -> Bluetooth headphones) and reopens capture on the new device.
* An explicit endpoint ID: capture only that device. It is never silently replaced; if it disconnects, capture
  pauses and reports it, and resumes automatically only when that same endpoint becomes active again.

The microphone is never opened: only the loopback twin of a playback (render) endpoint is used, and any input
device is rejected.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pyaudiowpatch as pyaudio

from audio import endpoints
from audio.wav import to_mono_16k, wav_bytes

log = logging.getLogger(__name__)
DEFAULT_DEVICE = "default"
WATCHDOG_INTERVAL_S = 1.0
# PortAudio initialise/terminate is not thread-safe. Every create/terminate in the process goes through this lock,
# so a capture being stopped (or its exiting watchdog) can never overlap a new capture starting.
_PA_LOCK = threading.Lock()


@dataclass
class AudioChunk:
    index: int
    offset_ms: int
    duration_ms: int
    wav: bytes


class LoopbackError(RuntimeError):
    pass


@dataclass
class DeviceEvent:
    at: float
    kind: str           # opened | reopened | paused | resumed | error
    device: str
    gap_ms: int = 0
    detail: str = ""


def get_loopback_devices() -> list[dict]:
    """Active playback endpoints for the device picker: stable id, name, default flag, and ambiguity flag."""
    try:
        eps = endpoints.list_render_endpoints()
        default = endpoints.default_render_endpoint()
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not enumerate playback endpoints: %s", exc)
        return []
    names = [e.name for e in eps]
    return [{"endpoint_id": e.id, "name": e.name, "is_default": bool(default and e.id == default.id),
             "ambiguous": names.count(e.name) > 1 or not e.name} for e in eps]


def resolve_loopback(pa: pyaudio.PyAudio, selection: str | None) -> tuple[dict, endpoints.Endpoint]:
    """Map a selection (DEFAULT_DEVICE or endpoint ID) to (PortAudio loopback device info, endpoint).

    PortAudio exposes no endpoint ID, so the endpoint is matched to a PortAudio WASAPI output by EXACT friendly name,
    and the match must be unique among active endpoints and among PortAudio outputs — otherwise it is rejected
    rather than guessed.
    """
    if not selection or selection == DEFAULT_DEVICE:
        ep = endpoints.default_render_endpoint()
        if ep is None:
            raise LoopbackError("No default playback device is available.")
    else:
        ep = endpoints.endpoint_by_id(selection)
        if ep is None:
            raise LoopbackError("The selected playback device is no longer known to Windows. Choose it again.")
    if not ep.active:
        raise LoopbackError(f"'{ep.name or 'Selected device'}' is not connected.")
    active_same_name = [e for e in endpoints.list_render_endpoints() if e.name == ep.name]
    if len(active_same_name) != 1:
        raise LoopbackError(f"More than one active playback device is named '{ep.name}'; capture cannot tell them "
                            "apart safely. Rename one in Windows Sound settings or disable the other.")
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    outputs = []
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if (d["hostApi"] == wasapi["index"] and d["maxOutputChannels"] > 0 and not d.get("isLoopbackDevice")
                and d["name"] == ep.name):
            outputs.append(d)
    if len(outputs) != 1:
        raise LoopbackError(f"Could not match '{ep.name}' to exactly one audio output ({len(outputs)} matches).")
    loop = pa.get_wasapi_loopback_analogue_by_index(outputs[0]["index"])
    if not loop.get("isLoopbackDevice") or loop.get("maxInputChannels", 0) <= 0:
        raise LoopbackError(f"'{ep.name}' has no usable loopback endpoint.")  # never fall back to an input device
    return loop, ep


class LoopbackCapture:
    """Continuous system-audio capture emitting fixed-length mono 16 kHz WAV chunks, with device-change recovery."""

    def __init__(self, on_chunk: Callable[[AudioChunk], None], chunk_seconds: float = 2.0,
                 target_rate: int = 16_000, on_error: Callable[[str], None] | None = None,
                 device: str | None = DEFAULT_DEVICE,
                 on_activity: Callable[[bool, float], None] | None = None,
                 on_status: Callable[[str], None] | None = None,
                 on_frame: Callable[["np.ndarray", int, float], None] | None = None) -> None:
        self._on_chunk = on_chunk
        # Live mode: every frame (voiced or silent) as mono int16 at target_rate with its offset; on_chunk unused.
        self._on_frame = on_frame
        self._on_error = on_error or (lambda m: log.error(m))
        self._on_status = on_status or (lambda m: log.info(m))
        self._on_activity = on_activity
        self._chunk_seconds = chunk_seconds
        self._target_rate = target_rate
        self.selection = device or DEFAULT_DEVICE
        self.follow_default = self.selection == DEFAULT_DEVICE
        self._pa: pyaudio.PyAudio | None = None
        self._stream = None
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._dev_lock = threading.RLock()
        self._index = 0
        self._channels = 2
        self._rate = 48_000
        self._bytes_per_chunk = 0
        self.device_name = ""
        self.endpoint_id: str | None = None
        self.chunks_emitted = 0
        self.events: list[DeviceEvent] = []
        self._closed_at: float | None = None
        self._watchdog: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self.paused_reason: str | None = None

    @property
    def running(self) -> bool:
        return self._stream is not None

    @property
    def next_index(self) -> int:
        return self._index

    # ---------------------------------------------------------------- open/close
    def _open(self) -> None:
        with _PA_LOCK:
            self._pa = pyaudio.PyAudio()  # a fresh PortAudio instance sees newly (re)connected devices
        try:
            dev, ep = resolve_loopback(self._pa, self.selection)
            self._channels = int(dev["maxInputChannels"])
            self._rate = int(dev["defaultSampleRate"])
            self.device_name, self.endpoint_id = ep.name, ep.id
            self._bytes_per_chunk = int(self._rate * self._chunk_seconds) * self._channels * 2
            with self._lock:
                self._buffer.clear()
            self._stream = self._pa.open(format=pyaudio.paInt16, channels=self._channels, rate=self._rate, input=True,
                                         input_device_index=dev["index"], frames_per_buffer=1024,
                                         stream_callback=self._callback)
            log.info("Capturing loopback of %s (%d ch @ %d Hz)", self.device_name, self._channels, self._rate)
        except Exception:
            self._close(flush=False)
            raise

    def _close(self, flush: bool) -> None:
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:  # noqa: BLE001 — device may already be gone
                pass
            finally:
                self._stream = None
        if self._pa:
            try:
                with _PA_LOCK:
                    self._pa.terminate()
            finally:
                self._pa = None
        with self._lock:
            tail = bytes(self._buffer)
            self._buffer.clear()
        if flush and self._bytes_per_chunk and len(tail) >= self._bytes_per_chunk // 4:
            frame = self._channels * 2
            tail = tail[: len(tail) - len(tail) % frame]
            self._emit(tail, duration_s=len(tail) / (frame * self._rate))

    def start(self, start_index: int = 0) -> None:
        if self._stream:
            return
        self._index = start_index
        with self._dev_lock:
            self._open()
        self.events.append(DeviceEvent(time.time(), "opened", self.device_name))
        self._stop_evt.clear()
        self._watchdog = threading.Thread(target=self._watch, name="loopback-watchdog", daemon=True)
        self._watchdog.start()

    def stop(self, flush: bool = True) -> None:
        self._stop_evt.set()
        if self._watchdog and self._watchdog.is_alive() and threading.current_thread() is not self._watchdog:
            self._watchdog.join(timeout=3)
        with self._dev_lock:
            self._close(flush=flush)

    # ---------------------------------------------------------------- device watchdog
    def _reopen(self, reason: str) -> None:
        with self._dev_lock:
            if self._stop_evt.is_set():  # Stop won the race: do not reopen a stream after Stop
                return
            old = self.device_name
            t0 = time.time()
            self._close(flush=True)       # hand over buffered audio from the old device first
            try:
                self._open()
            except Exception as exc:  # noqa: BLE001
                self._closed_at = t0
                self.paused_reason = str(exc)
                self._activity_idle()
                self.events.append(DeviceEvent(time.time(), "paused", old, detail=str(exc)))
                self._on_error(f"Capture paused: {exc}")
                return
            gap = int((time.time() - t0) * 1000)
            self.paused_reason = None
            self.events.append(DeviceEvent(time.time(), "reopened", self.device_name, gap, f"{reason} (was {old})"))
            self._on_status(f"Capture switched to {self.device_name} ({reason}; gap {gap} ms)")

    def _watch(self) -> None:
        while not self._stop_evt.wait(WATCHDOG_INTERVAL_S):
            try:
                if self._stream is None:  # paused after a disconnect: resume when possible
                    target = (endpoints.default_render_endpoint() if self.follow_default
                              else endpoints.endpoint_by_id(self.selection))
                    if target is not None and target.active:
                        t_paused = self._closed_at or time.time()
                        with self._dev_lock:
                            if self._stop_evt.is_set():
                                break
                            try:
                                self._open()
                            except Exception as exc:  # noqa: BLE001
                                log.debug("Resume not yet possible: %s", exc)
                                continue
                        gap = int((time.time() - t_paused) * 1000)
                        self.paused_reason, self._closed_at = None, None
                        self.events.append(DeviceEvent(time.time(), "resumed", self.device_name, gap))
                        self._on_status(f"Capture resumed on {self.device_name} (gap {gap} ms)")
                    continue
                if self.follow_default:
                    cur = endpoints.default_render_endpoint()
                    if cur is None:
                        self._closed_at = time.time()
                        with self._dev_lock:
                            self._close(flush=True)
                        self.paused_reason = "No playback device"
                        self._activity_idle()
                        self.events.append(DeviceEvent(time.time(), "paused", self.device_name, detail="no default"))
                        self._on_error("Capture paused: no playback device is available.")
                    elif cur.id != self.endpoint_id:
                        self._reopen("Windows default output changed")
                else:
                    ep = endpoints.endpoint_by_id(self.selection)
                    if ep is None or not ep.active:
                        self._closed_at = time.time()
                        with self._dev_lock:
                            self._close(flush=True)
                        self.paused_reason = f"'{self.device_name}' disconnected"
                        self._activity_idle()
                        self.events.append(DeviceEvent(time.time(), "paused", self.device_name, detail="disconnected"))
                        self._on_error(f"Capture paused: '{self.device_name}' disconnected. It will resume when "
                                       "that same device reconnects (the selected device is never replaced).")
                    elif self._stream is not None and not self._stream.is_active():
                        self._reopen("stream stopped unexpectedly")
            except Exception as exc:  # noqa: BLE001 — the watchdog must never kill capture
                log.warning("Device watchdog check failed: %s", exc)

    def _activity_idle(self) -> None:
        if self._on_activity:
            try:
                self._on_activity(False, 0.0)
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- audio path
    def _callback(self, in_data, frame_count, time_info, status):
        with self._lock:
            self._buffer.extend(in_data)
            while self._bytes_per_chunk and len(self._buffer) >= self._bytes_per_chunk:
                raw = bytes(self._buffer[: self._bytes_per_chunk])
                del self._buffer[: self._bytes_per_chunk]
                self._emit(raw)
        return (None, pyaudio.paContinue)

    def _emit(self, raw: bytes, duration_s: float | None = None) -> None:
        index = self._index
        self._index += 1  # index advances even for silent chunks so offsets stay accurate
        duration_s = self._chunk_seconds if duration_s is None else duration_s
        samples = to_mono_16k(raw, self._channels, self._rate, self._target_rate)
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) if samples.size else 0.0
        silent = rms < 40.0
        if self._on_activity:
            try:
                self._on_activity(not silent, rms)
            except Exception:  # noqa: BLE001
                pass
        if self._on_frame is not None:
            try:
                self._on_frame(samples, int(index * self._chunk_seconds * 1000), rms)
                self.chunks_emitted += 1
            except Exception as exc:  # noqa: BLE001
                self._on_error(f"Live frame handler failed: {exc}")
            return
        if silent or samples.size == 0:
            return
        try:
            self._on_chunk(AudioChunk(index, int(index * self._chunk_seconds * 1000), int(duration_s * 1000),
                                      wav_bytes(samples, self._target_rate)))
            self.chunks_emitted += 1
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Chunk handler failed: {exc}")

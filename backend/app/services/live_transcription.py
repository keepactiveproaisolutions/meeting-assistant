"""Live mode: relay desktop system-audio to OpenAI Realtime transcription (gpt-realtime-whisper).

Desktop  --(ws: JSON control + binary PCM16 24 kHz mono)-->  backend  --(wss)-->  OpenAI Realtime
                                                             backend  --(meeting broadcast)--> all clients

* The OpenAI API key never leaves the backend.
* One paid stream per meeting: a new Live connection closes the previous one first.
* gpt-realtime-whisper has no server VAD (turn_detection must be null), so the desktop detects pauses and sends
  {"type":"commit"}. Deltas stream as provisional `transcript.partial` events; each completed turn is persisted through
  the same path as Standard mode (raw segment -> paragraphs -> question detection) and its partial is then cleared.
* Stop / disconnect: commit the open turn, wait (bounded) for outstanding finals, close the OpenAI socket.
* No automatic reconnect to OpenAI here: failures are reported to the desktop, which decides (bounded) what to do.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from uuid import UUID

import websockets

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import Meeting
from app.realtime.events import Event
from app.realtime.manager import manager

log = logging.getLogger("live")
OPENAI_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
import os
import re

# gpt-live-transcribe: OpenAI's recommended live model; supports keywords (document vocabulary).
# gpt-realtime-whisper: kept selectable (MA_LIVE_MODEL) — both measured on the same audio, see report.
MODEL = os.getenv("MA_LIVE_MODEL", "gpt-live-transcribe")
PRICE_PER_MIN = 0.017  # USD/min for both models, OpenAI pricing page (checked 2026-11-15)
FINALIZE_TIMEOUT_S = 6.0
BYTES_PER_SECOND = 24_000 * 2


@dataclass
class Turn:
    seq: int
    start_ms: int
    end_ms: int | None = None
    item_id: str | None = None
    text: str = ""
    audio_bytes: int = 0  # PCM received for this turn -> its true end when the client never committed
    committed_at: float | None = None
    first_delta_at: float | None = None


@dataclass
class LiveSession:
    meeting_id: UUID
    delay: str = "low"
    language: str | None = "en"
    keywords: list[str] = field(default_factory=list)
    confirmed_model: str = ""
    turns: list[Turn] = field(default_factory=list)
    by_item: dict[str, Turn] = field(default_factory=dict)
    seq: int = 0
    pending_bytes: int = 0
    appended_bytes: int = 0
    billed_seconds: float = 0.0
    finals: int = 0
    oa: object | None = None
    closed: bool = False
    _all_done: asyncio.Event = field(default_factory=asyncio.Event)
    _persist_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stats: list[dict] = field(default_factory=list)

    # ---- provisional text visible to (re)joining clients ----
    def partials(self) -> list[dict]:
        return [{"item_id": t.item_id, "text": t.text, "start_offset_ms": t.start_ms}
                for t in self.turns if t.item_id and t.text]

    async def open(self) -> None:
        key = get_settings().openai_api_key
        self.oa = await websockets.connect(OPENAI_URL, additional_headers={"Authorization": f"Bearer {key}"},
                                           max_size=None, open_timeout=10, ping_interval=20)
        transcription: dict = {"model": MODEL, "delay": self.delay}
        if MODEL == "gpt-live-transcribe":
            if self.language:
                transcription["languages"] = [self.language]
            kws = [k for k in (re.sub(r"[<>\r\n]", " ", k).strip() for k in self.keywords) if k][:100]
            if kws:
                transcription["keywords"] = kws
        elif self.language:
            transcription["language"] = self.language
        log.info("live %s: opening %s delay=%s keywords=%d", self.meeting_id, MODEL, self.delay,
                 len(transcription.get("keywords", [])))
        await self.oa.send(json.dumps({"type": "session.update", "session": {
            "type": "transcription",
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000},
                                "transcription": transcription, "turn_detection": None}}}}))
        # Wait for the server to accept the configuration (or report an error) before audio flows.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ev = json.loads(await asyncio.wait_for(self.oa.recv(), timeout=deadline - time.monotonic()))
            if ev.get("type") == "session.updated":
                # Log what OpenAI actually configured (not just what we asked for).
                tr = (((ev.get("session") or {}).get("audio") or {}).get("input") or {}).get("transcription") or {}
                self.confirmed_model = tr.get("model") or "?"
                log.info("live %s: OpenAI confirmed session model=%s delay=%s turn_detection=%s", self.meeting_id,
                         self.confirmed_model, tr.get("delay"),
                         (((ev.get("session") or {}).get("audio") or {}).get("input") or {}).get("turn_detection"))
                if self.confirmed_model != MODEL:
                    log.warning("live %s: requested %s but OpenAI reports %s", self.meeting_id, MODEL,
                                self.confirmed_model)
                return
            if ev.get("type") == "error":
                raise RuntimeError(ev.get("error", {}).get("message", "Realtime session rejected"))
        raise TimeoutError("Realtime session was not confirmed")

    # ---- desktop -> OpenAI ----
    def _open_turn(self) -> Turn | None:
        return self.turns[-1] if self.turns and self.turns[-1].end_ms is None else None

    async def begin_turn(self, offset_ms: int) -> None:
        if self._open_turn():
            return
        log.info("live %s: turn begins at %s ms", self.meeting_id, offset_ms)
        self.seq += 1
        self.turns.append(Turn(self.seq, int(offset_ms)))
        self._all_done.clear()

    async def append(self, pcm: bytes) -> None:
        if not self._open_turn() or not pcm:
            return  # audio outside a turn is never sent (and never billed)
        await self.oa.send(json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()}))
        self.pending_bytes += len(pcm)
        self._open_turn().audio_bytes += len(pcm)
        self.appended_bytes += len(pcm)

    async def commit(self, end_offset_ms: int) -> None:
        turn = self._open_turn()
        if not turn:
            return
        turn.end_ms = max(int(end_offset_ms), turn.start_ms + 100)
        turn.committed_at = time.perf_counter()
        if self.pending_bytes == 0:  # committing an empty buffer is an API error
            self.turns.remove(turn)
            self._check_done()
            return
        self.pending_bytes = 0
        await self.oa.send(json.dumps({"type": "input_audio_buffer.commit"}))

    # ---- OpenAI -> clients ----
    def _turn_for(self, item_id: str) -> Turn | None:
        t = self.by_item.get(item_id)
        if t is None:  # items are created in turn order: bind to the oldest unbound turn
            t = next((x for x in self.turns if x.item_id is None), None)
            if t is not None:
                t.item_id = item_id
                self.by_item[item_id] = t
        return t

    async def pump(self) -> None:
        """Read OpenAI events until the socket closes."""
        async for raw in self.oa:
            ev = json.loads(raw)
            kind = ev.get("type", "")
            if kind == "conversation.item.input_audio_transcription.delta":
                t = self._turn_for(ev.get("item_id", ""))
                if t is None:
                    continue
                if t.first_delta_at is None:
                    t.first_delta_at = time.perf_counter()
                    log.info("live %s: first delta turn %s (start %s ms)", self.meeting_id, t.seq, t.start_ms)
                t.text += ev.get("delta", "")
                await manager.broadcast(self.meeting_id, Event.TRANSCRIPT_PARTIAL,
                                        {"item_id": t.item_id, "text": t.text.strip(), "start_offset_ms": t.start_ms,
                                         "final": False})
            elif kind == "input_audio_buffer.committed":
                self._turn_for(ev.get("item_id", ""))
            elif kind == "conversation.item.input_audio_transcription.completed":
                t = self._turn_for(ev.get("item_id", ""))
                usage = ev.get("usage") or {}
                if usage.get("type") == "duration":
                    self.billed_seconds += float(usage.get("seconds", 0))
                if t is not None:
                    await self._finalize(t, ev.get("transcript", "") or t.text)
            elif kind == "conversation.item.input_audio_transcription.failed":
                t = self._turn_for(ev.get("item_id", ""))
                msg = (ev.get("error") or {}).get("message", "transcription failed")
                log.warning("live %s: turn failed: %s", self.meeting_id, msg)
                if t is not None:  # keep whatever streamed so nothing silently disappears
                    await self._finalize(t, t.text)
            elif kind == "error":
                msg = (ev.get("error") or {}).get("message", "unknown")
                log.warning("live %s: OpenAI error: %s", self.meeting_id, msg)
                await manager.broadcast(self.meeting_id, Event.ERROR, {"message": f"Live transcription: {msg}"})

    async def _finalize(self, t: Turn, text: str) -> None:
        from app.services import meeting_service
        done_at = time.perf_counter()
        async with self._persist_lock:
            async with SessionLocal() as db:
                meeting = await db.get(Meeting, self.meeting_id)
                end_ms = t.end_ms if t.end_ms is not None else t.start_ms + t.audio_bytes * 1000 // BYTES_PER_SECOND
                if meeting is not None and text.strip():
                    await meeting_service.persist_transcript_text(
                        db, meeting, text, t.seq, t.start_ms, max(100, end_ms - t.start_ms))
            # The final segment is broadcast before the provisional text is cleared: no gap, no duplicate.
            await manager.broadcast(self.meeting_id, Event.TRANSCRIPT_PARTIAL,
                                    {"item_id": t.item_id, "text": "", "start_offset_ms": t.start_ms, "final": True})
        self.finals += 1
        stat = {"seq": t.seq, "audio_ms": (t.end_ms or t.start_ms) - t.start_ms,
                "commit_to_final_ms": int((done_at - t.committed_at) * 1000) if t.committed_at else None,
                "chars": len(text)}
        self.stats.append(stat)
        log.info("live %s: final turn %s %s", self.meeting_id, t.seq, stat)
        if t in self.turns:
            self.turns.remove(t)
        if t.item_id:
            self.by_item.pop(t.item_id, None)
        self._check_done()

    def _check_done(self) -> None:
        if not any(t.end_ms is not None for t in self.turns) and not self._open_turn():
            self._all_done.set()

    async def finish(self, end_offset_ms: int | None) -> None:
        """Bounded finalization: commit the open turn, wait for outstanding finals, then close the paid stream."""
        if self.closed:
            return
        try:
            if self._open_turn() and self.oa is not None:
                t = self._open_turn()
                await self.commit(end_offset_ms if end_offset_ms is not None
                                  else t.start_ms + t.audio_bytes * 1000 // BYTES_PER_SECOND)
            self._check_done()
            if self.turns:
                await asyncio.wait_for(self._all_done.wait(), timeout=FINALIZE_TIMEOUT_S)
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            log.warning("live %s: finalize incomplete (%s); %d turn(s) keep streamed text",
                        self.meeting_id, type(exc).__name__, len(self.turns))
            for t in list(self.turns):  # never lose text that was already shown as provisional
                if t.text.strip():
                    await self._finalize(t, t.text)
        await self.close()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.oa is not None:
            # The close handshake with OpenAI took ~2.3 s in tests; nothing more is sent or billed after all
            # finals, so finish it in the background (bounded) instead of delaying Stop.
            oa = self.oa

            async def _close_oa():
                try:
                    await asyncio.wait_for(oa.close(), timeout=5)
                except Exception:  # noqa: BLE001
                    oa.transport.abort() if getattr(oa, "transport", None) else None
            task = asyncio.create_task(_close_oa())
            _bg.add(task)
            task.add_done_callback(_bg.discard)
        for t in list(self.turns):  # clear any provisional text left on clients
            if t.item_id:
                await manager.broadcast(self.meeting_id, Event.TRANSCRIPT_PARTIAL,
                                        {"item_id": t.item_id, "text": "", "start_offset_ms": t.start_ms,
                                         "final": True})
        sent_s = self.appended_bytes / BYTES_PER_SECOND
        log.info("live %s: closed. audio sent %.1f s, billed %.1f s (~$%.4f), finals %d",
                 self.meeting_id, sent_s, self.billed_seconds, self.billed_seconds / 60 * PRICE_PER_MIN, self.finals)
        if sessions.get(str(self.meeting_id)) is self:
            sessions.pop(str(self.meeting_id), None)

    def usage(self) -> dict:
        return {"audio_sent_s": round(self.appended_bytes / BYTES_PER_SECOND, 1),
                "billed_s": self.billed_seconds,
                "est_usd": round(self.billed_seconds / 60 * PRICE_PER_MIN, 5), "finals": self.finals}


sessions: dict[str, LiveSession] = {}
_bg: set = set()  # background close tasks (strong refs)


def active(meeting_id: UUID | str) -> bool:
    s = sessions.get(str(meeting_id))
    return bool(s and not s.closed)


def partials(meeting_id: UUID | str) -> list[dict]:
    s = sessions.get(str(meeting_id))
    return s.partials() if s and not s.closed else []

"""WebSocket endpoint: /ws/meetings/{meeting_id}?token=<jwt>"""
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from fastapi import HTTPException

from app.core.database import SessionLocal
from app.core.deps import meeting_access, user_from_token
from app.models import Meeting
from app.realtime.events import Event, envelope
from app.realtime.manager import manager
from app.services import answer_service, meeting_service

router = APIRouter()


@router.websocket("/ws/meetings/{meeting_id}")
async def meeting_socket(ws: WebSocket, meeting_id: UUID, token: str = Query(default="")):
    async with SessionLocal() as db:
        user = await user_from_token(token, db)
        if not user:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        try:
            meeting, _ = await meeting_access(db, meeting_id, user)
        except HTTPException:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        user_id = user.id
        owner_id = meeting.created_by  # documents (and the prompt-cache warmer) belong to the meeting owner
        # Register first, then snapshot: events broadcast meanwhile are either already in the snapshot or
        # delivered after it (clients de-duplicate by id / answer offset).
        await manager.connect(meeting_id, ws)
        snap = await meeting_service.snapshot(db, meeting)

    await ws.send_json(envelope(Event.SNAPSHOT, meeting_id, snap.model_dump(mode="json")))
    answer_service.ensure_warmer(meeting_id, owner_id)
    await manager.broadcast(meeting_id, Event.PRESENCE, {"connections": manager.count(meeting_id)})
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == Event.PING:
                await ws.send_json(envelope(Event.PONG, meeting_id))
            elif kind == Event.ASK:
                data = msg.get("data") or {}
                question = str(data.get("question", "")).strip()[:4000]
                if not question:
                    await ws.send_json(envelope(Event.ERROR, meeting_id, {"message": "question is required"}))
                    continue
                async with SessionLocal() as db:
                    meeting = await db.get(Meeting, meeting_id)
                    answer = await answer_service.create_answer(
                        db, meeting, user_id, question,
                        (str(data.get("selected_text") or "")[:8000] or None), None)
                answer_service.schedule_answer(answer.id)
    except WebSocketDisconnect:
        pass
    except Exception:  # malformed JSON, or the socket already closed underneath us
        try:
            await ws.close(code=status.WS_1003_UNSUPPORTED_DATA)
        except Exception:  # noqa: BLE001 - already closed
            pass
    finally:
        manager.disconnect(meeting_id, ws)
        await manager.broadcast(meeting_id, Event.PRESENCE, {"connections": manager.count(meeting_id)})


@router.websocket("/ws/meetings/{meeting_id}/live-audio")
async def live_audio_socket(ws: WebSocket, meeting_id: UUID, token: str = Query(default=""),
                            delay: str = Query(default="low")):
    """Live mode audio relay (desktop only). Text frames: {"type":"turn","offset_ms"}, {"type":"commit",
    "end_offset_ms"}, {"type":"stop","end_offset_ms"}. Binary frames: PCM16 24 kHz mono for the open turn."""
    import asyncio
    import json
    import logging
    from app.services import live_transcription as lt
    log = logging.getLogger("live")
    async with SessionLocal() as db:
        user = await user_from_token(token, db)
        if not user:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        try:
            meeting, is_member = await meeting_access(db, meeting_id, user)
        except HTTPException:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        if not is_member or meeting.status != "live":
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        import os
        from app.services.live_keywords import filter_keywords
        vocab = []
        if os.getenv("MA_LIVE_KEYWORDS", "off") == "auto":  # measured: doc keywords hurt general terms
            vocab = filter_keywords(await meeting_service._meeting_vocabulary(db, meeting), [user.full_name])
    await ws.accept()

    # One paid stream per meeting: finish any previous Live session before opening a new one.
    old = lt.sessions.get(str(meeting_id))
    if old is not None and not old.closed:
        log.info("live %s: replacing existing session", meeting_id)
        await old.finish(None)
    if delay not in ("minimal", "low", "medium", "high", "xhigh"):
        delay = "low"
    sess = lt.LiveSession(meeting_id, delay=delay, keywords=vocab)
    lt.sessions[str(meeting_id)] = sess
    try:
        await sess.open()
    except Exception as exc:  # noqa: BLE001
        log.warning("live %s: open failed: %s", meeting_id, exc)
        await sess.close()
        try:
            await ws.send_json({"type": "error", "fatal": True, "message": f"Live transcription unavailable: {exc}"})
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
        return
    await ws.send_json({"type": "ready", "model": sess.confirmed_model or lt.MODEL, "delay": delay})
    pump = asyncio.create_task(sess.pump())
    end_offset: int | None = None
    reason = "client closed"
    try:
        while True:
            recv = asyncio.create_task(ws.receive())
            done, _ = await asyncio.wait({recv, pump}, return_when=asyncio.FIRST_COMPLETED)
            if pump in done:
                recv.cancel()
                reason = f"OpenAI stream ended ({pump.exception() if not pump.cancelled() and pump.exception() else 'closed'})"
                await ws.send_json({"type": "error", "fatal": True, "message": reason})
                break
            msg = recv.result()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes"):
                await sess.append(msg["bytes"])
            elif msg.get("text"):
                c = json.loads(msg["text"])
                kind = c.get("type")
                if kind == "turn":
                    await sess.begin_turn(int(c.get("offset_ms", 0)))
                elif kind == "commit":
                    await sess.commit(int(c.get("end_offset_ms", 0)))
                elif kind == "stop":
                    end_offset = c.get("end_offset_ms")
                    reason = "stop"
                    break
    except Exception as exc:  # noqa: BLE001 - socket dropped / bad frame: still finalize below
        reason = f"{type(exc).__name__}: {exc}"
    finally:
        log.info("live %s: finishing (%s)", meeting_id, reason)
        await sess.finish(end_offset)        # bounded: commit open turn, wait for finals, close paid stream
        pump.cancel()
        try:
            await ws.send_json({"type": "stopped", "usage": sess.usage()})
            await ws.close()
        except Exception:  # noqa: BLE001
            pass

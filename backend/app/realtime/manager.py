"""In-process WebSocket hub, one room per meeting.

For multi-instance deployments swap `broadcast` for a Redis pub/sub fan-out;
callers only depend on this interface.
"""
import asyncio
import logging
from collections import defaultdict
from typing import Any
from uuid import UUID

from fastapi import WebSocket

from app.realtime.events import Event, envelope

log = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._rooms: dict[UUID, set[WebSocket]] = defaultdict(set)

    def count(self, meeting_id: UUID) -> int:
        return len(self._rooms.get(meeting_id, ()))

    async def connect(self, meeting_id: UUID, ws: WebSocket) -> None:
        await ws.accept()
        self._rooms[meeting_id].add(ws)

    def disconnect(self, meeting_id: UUID, ws: WebSocket) -> None:
        room = self._rooms.get(meeting_id)
        if room:
            room.discard(ws)
            if not room:
                del self._rooms[meeting_id]

    async def _send(self, meeting_id: UUID, ws: WebSocket, message: dict[str, Any]) -> None:
        try:
            await ws.send_json(message)
        except Exception:  # client went away
            self.disconnect(meeting_id, ws)

    async def broadcast(self, meeting_id: UUID, event: Event, data: dict[str, Any] | None = None) -> None:
        message = envelope(event, meeting_id, data)
        await asyncio.gather(*(self._send(meeting_id, ws, message) for ws in list(self._rooms.get(meeting_id, ()))))


manager = ConnectionManager()

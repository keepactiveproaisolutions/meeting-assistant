"""WebSocket client (runs in a thread, auto-reconnects) delivering events to a callback."""
import json
import logging
import threading
import time
from collections.abc import Callable

import websocket

from config.settings import settings

log = logging.getLogger(__name__)


class RealtimeClient:
    def __init__(self, token: str | None, meeting_id: str, on_event: Callable[[dict], None],
                 on_status: Callable[[str], None]) -> None:
        token_str = token or ""
        self._url = f"{settings.ws_url}/ws/meetings/{meeting_id}?token={token_str}"
        self._on_event = on_event
        self._on_status = on_status
        self._stop = threading.Event()
        self._ws: websocket.WebSocketApp | None = None
        self._thread = threading.Thread(target=self._run, name="realtime", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            self._ws.close()

    def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            self._ws = websocket.WebSocketApp(
                self._url,
                on_open=lambda ws: self._on_status("connected"),
                on_message=self._message,
                on_close=lambda ws, *a: self._on_status("disconnected"))
            self._ws.run_forever(ping_interval=20, ping_timeout=10)
            if self._stop.is_set():
                break
            self._on_status("reconnecting")
            time.sleep(delay)
            delay = min(delay * 2, 15)

    def _message(self, _ws, raw: str) -> None:
        try:
            self._on_event(json.loads(raw))
        except Exception:  # noqa: BLE001
            log.exception("Bad realtime message")

"""Runs one Screen Intelligence request: hide our windows, capture, upload, report status.

The answer itself arrives through the normal meeting WebSocket and shows in the usual answer panel (desktop and web),
with the same session memory as every other question.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from screen_intelligence import capture

log = logging.getLogger(__name__)
HIDE_DELAY_MS = 300  # let the compositor repaint without our window before grabbing


class ScreenIntelligence(QObject):
    status = Signal(str)
    _finished = Signal(str)

    def __init__(self, api, get_meeting_id: Callable[[], str | None], get_windows: Callable[[], list]) -> None:
        super().__init__()
        self._api = api
        self._meeting_id = get_meeting_id
        self._windows = get_windows
        self._busy = False
        self._finished.connect(self._done)

    def analyze(self, note: str = "", monitor: str = capture.AUTO) -> None:
        if self._busy:
            return
        meeting_id = self._meeting_id()
        if not meeting_id:
            self.status.emit("Start or join a meeting first, then use Screen.")
            return
        self._busy = True
        self.status.emit("Capturing screen…")
        wins = [w for w in self._windows() if w is not None and w.isVisible()]
        self._window_screen = wins[0].screen() if wins else None
        for w in wins:
            w.setWindowOpacity(0.0)  # invisible for the grab, restored right after; no focus change
        QTimer.singleShot(HIDE_DELAY_MS, lambda: self._capture(meeting_id, note, monitor, wins))

    def _capture(self, meeting_id: str, note: str, monitor: str, wins: list) -> None:
        try:
            jpeg = capture.grab_jpeg(capture.pick_screen(monitor, self._window_screen))
        except Exception as exc:  # noqa: BLE001
            log.exception("screen capture failed")
            self._finished.emit(f"Screen capture failed: {exc}")
            return
        finally:
            for w in wins:
                w.setWindowOpacity(1.0)
        self.status.emit(f"Screen captured ({len(jpeg) // 1024} KB) — analysing…")
        threading.Thread(target=self._upload, args=(meeting_id, jpeg, note), daemon=True,
                         name="screen-upload").start()

    def _upload(self, meeting_id: str, jpeg: bytes, note: str) -> None:
        try:
            self._api.screen_ask(meeting_id, jpeg, note)
            self._finished.emit("Screen sent — the answer will appear below.")
        except Exception as exc:  # noqa: BLE001
            self._finished.emit(f"Screen analysis failed: {exc}")

    def _done(self, message: str) -> None:
        self._busy = False
        self.status.emit(message)

"""Application controller + entrypoint. Run from desktop-app/: python -m main.app"""
import logging
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from ai.api_client import ApiClient, ApiError
from ai.realtime_client import RealtimeClient
from audio.loopback_capture import AudioChunk, LoopbackCapture, get_loopback_devices
from config.settings import settings
from screen_intelligence.controller import ScreenIntelligence
from transcription.live_streamer import LiveStreamer
from transcription.uploader import ChunkUploader
from ui.login_dialog import LoginDialog
from ui.main_window import MainWindow

log = logging.getLogger(__name__)


class Bridge(QObject):
    """Marshals events from worker threads onto the Qt main thread."""
    event = Signal(dict)
    status = Signal(str)
    error = Signal(str)
    activity = Signal(bool, float)
    live_failed = Signal(str)


class Controller:
    def __init__(self, api: ApiClient, window: MainWindow, workspace_id: str | None = None) -> None:
        self.api = api
        self.win = window
        self.workspace_id = workspace_id
        self.meeting: dict | None = None
        self.capture: LoopbackCapture | None = None
        self.uploader: ChunkUploader | None = None
        self.live: LiveStreamer | None = None
        self.capture_mode = "standard"
        self.realtime: RealtimeClient | None = None
        self.bridge = Bridge()
        self.bridge.event.connect(self.on_event)
        self.bridge.status.connect(lambda s: self.win.set_status(f"Realtime: {s}"))
        self.bridge.error.connect(lambda m: self.win.set_status(m))
        self.bridge.activity.connect(self.win.set_audio_activity)
        self.bridge.live_failed.connect(self._on_live_failed)

        self.is_capturing = False
        self.next_offset_ms = 0  # meeting timeline position where the next capture continues
        # Enumerate and populate WASAPI loopback output devices
        self.win.populate_devices(get_loopback_devices())

        window.create_clicked.connect(self.create_and_start)
        window.join_clicked.connect(self.join_and_start)
        window.toggle_capture_clicked.connect(self.toggle_capture)
        window.end_meeting_clicked.connect(self.end_meeting)
        window.stop_clicked.connect(self.end_meeting)
        window.send_clicked.connect(self.ask)

        # AI Screen Intelligence & Coding Copilot (self-contained package: screen_intelligence/)
        # The Live Screen panel lives inside the main window (window.live_panel); answers use the normal answer panel.
        self.screen_ai = ScreenIntelligence(self.api, lambda: self.meeting["id"] if self.meeting else None,
                                            lambda: [self.win, self.win.floating_win])
        self.screen_ai.status.connect(self.win.set_status)
        self.screen_ai.status.connect(self.win.live_panel.set_status)
        window.screen_clicked.connect(lambda note, monitor: self.screen_ai.analyze(note, monitor))

    def _setup_session(self, meeting_data: dict) -> None:
        """Join/create never starts audio capture — the user must click Start Transcription."""
        # Switching meetings: stop capture (drains pending uploads to the OLD meeting) and close the old realtime
        # subscription first, so events/audio of two meetings can never mix.
        if self.is_capturing or self.capture:
            self.stop_capture(user_initiated=False)
        if self.realtime:
            self.realtime.stop()
            self.realtime = None
        self.meeting = meeting_data
        mid = self.meeting["id"]
        self.win.current_meeting_id = mid
        self.win.transcript.clear()
        self.next_offset_ms = 0
        self.win.show_meeting(self.meeting["code"], self.meeting.get("join_url", ""))

        self.realtime = RealtimeClient(self.api.token, mid, self.bridge.event.emit, self.bridge.status.emit)
        self.realtime.start()

        self.win.set_running(True)
        self.win.set_capturing(False, finishing=False)
        self.win.set_status("Joined — click Start Transcription to capture system audio")

    def start_capture(self, force_mode: str | None = None) -> None:
        if not self.meeting:
            return
        if self.capture or self.live or self.uploader:  # never two capture pipelines / paid streams
            log.warning("start_capture ignored: a capture pipeline is still active")
            return
        mid = self.meeting["id"]

        # Refresh the picker (selection is kept by stable endpoint ID)
        chosen = self.win.get_selected_device()          # exactly what the picker shows right now
        self.win.settings.setValue("audio/endpoint_id", chosen)
        self.win.populate_devices(get_loopback_devices(), selected_idx=chosen)
        selected_dev = self.win.get_selected_device()
        if chosen != "default" and selected_dev == "default":
            QMessageBox.warning(self.win, "Playback device not connected",
                                "The playback device you selected is not connected. Connect it, or choose another "
                                "device (or System default), then click Start again.")
            return

        self.win.set_capturing(True)
        mode = force_mode or self.win.get_mode()
        if mode == "live":
            # Non-blocking: audio is buffered while the stream connects; a failure falls back to Standard.
            self.live = LiveStreamer(self.api.token, mid, on_fatal=self.bridge.live_failed.emit)
            self.live.start()
        self.capture_mode = mode
        if mode == "live":
            self.capture = LoopbackCapture(
                lambda _c: None, 0.1, 24_000,
                on_error=self.bridge.error.emit, device=selected_dev,
                on_activity=lambda act, rms: self.bridge.activity.emit(act, rms),
                on_status=self.bridge.error.emit, on_frame=self.live.feed)
            start_index = -(-self.next_offset_ms // 100)
        else:
            self.uploader = ChunkUploader(self.api.token, mid, on_error=self.bridge.error.emit)
            self.uploader.start()
            self.capture = LoopbackCapture(
                self._on_chunk, settings.chunk_seconds, 16_000,
                on_error=self.bridge.error.emit, device=selected_dev,
                on_activity=lambda act, rms: self.bridge.activity.emit(act, rms),
                on_status=self.bridge.error.emit)
            start_index = -(-self.next_offset_ms // int(settings.chunk_seconds * 1000))
        try:
            self.capture.start(start_index=start_index)
            self.is_capturing = True
            label = "Live (streaming)" if self.live else "Standard"
            self.win.set_status(f"Capturing ({label}): {self.capture.device_name}")
        except Exception as exc:  # noqa: BLE001
            self.stop_capture(user_initiated=False)
            QMessageBox.critical(self.win, "Audio Capture Failed", f"WASAPI loopback capture error: {exc}")

    def stop_capture(self, user_initiated: bool = False, live_failed: bool = False) -> None:
        if not self.is_capturing and not self.capture:
            return
        self.is_capturing = False
        self.win.set_capturing(False, finishing=True)
        self.win.set_status("Finishing transcript...")

        if self.capture:
            self.capture.stop(flush=True)          # stop new audio + hand over the trailing partial chunk
            step_ms = 100 if self.capture_mode == "live" else int(settings.chunk_seconds * 1000)
            self.next_offset_ms = max(self.next_offset_ms, self.capture.next_index * step_ms)
            self.capture = None

        if self.live:
            if live_failed:
                self.live.abort()                  # dead/failing stream: don't wait; backend finalizes sent turns
            else:
                usage = self.live.stop()           # commit open turn, bounded finalization, close paid stream
                log.info("Live stream closed; usage %s", usage)
            self.live = None
        if self.uploader:
            self.uploader.stop(drain=True)
            self.uploader = None

        self.win.set_capturing(False, finishing=False)
        label = "Transcription paused" if user_initiated else "Capture stopped"
        self.win.transcript.add_interval_marker(label)
        if self.win.floating_win:
            self.win.floating_win.transcript.add_interval_marker(label)
        self.win.set_status(f"{label} (meeting still active)")

    def toggle_capture(self) -> None:
        if not self.meeting:
            return
        if self.is_capturing:
            self.stop_capture(user_initiated=True)
        else:
            self.win.transcript.add_interval_marker("Transcription resumed")
            if self.win.floating_win:
                self.win.floating_win.transcript.add_interval_marker("Transcription resumed")
            self.start_capture()

    def create_and_start(self, title: str) -> None:
        try:
            if not self.workspace_id:
                ws_list = self.api.workspaces()
                if ws_list:
                    self.workspace_id = ws_list[0]["id"]
            if not self.workspace_id:
                raise ApiError("No workspace available to create meeting")
            meeting = self.api.create_meeting(self.workspace_id, title)
            self._setup_session(meeting)
        except ApiError as exc:
            QMessageBox.critical(self.win, "Create Meeting Error", str(exc))

    def join_and_start(self, code: str) -> None:
        try:
            meeting = self.api.join_meeting(code)
            self._setup_session(meeting)
        except ApiError as exc:
            QMessageBox.critical(self.win, "Join Meeting Error", str(exc))

    def stop(self) -> None:
        if self.is_capturing:
            self.stop_capture(user_initiated=False)
        if self.realtime:
            self.realtime.stop()
            self.realtime = None

    def end_meeting(self) -> None:
        if self.is_capturing:
            self.stop_capture(user_initiated=False)
        if self.realtime:
            self.realtime.stop()
            self.realtime = None
        if self.meeting:
            try:
                self.api.end_meeting(self.meeting["id"])
            except ApiError:
                pass
            self.meeting = None
        self.win.set_running(False)
        self.win.transcript.add_interval_marker("Meeting ended")
        if self.win.floating_win:
            self.win.floating_win.transcript.add_interval_marker("Meeting ended")
        self.win.set_status("Meeting ended")

    def _on_live_failed(self, message: str) -> None:
        """Live stream died mid-capture: close it and continue in Standard mode (once), never overlapping."""
        if not self.is_capturing or self.capture_mode != "live":
            return
        log.warning("Live failed: %s - switching to Standard", message)
        self.stop_capture(user_initiated=False, live_failed=True)
        self.win.transcript.clear_partials()
        self.start_capture(force_mode="standard")
        self.win.set_status(f"Live transcription stopped ({message}). Continuing in Standard mode.")

    def _log_live_timing(self, data: dict) -> None:
        """Measured on this desktop: first voiced frame -> first provisional text; commit -> final text."""
        import time
        if not self.live:
            return
        w = self.live.turn_wall.get(int(data.get("start_offset_ms", -1)))
        if not w:
            return
        now = time.perf_counter()
        tl = logging.getLogger("timing.live")
        if not data.get("final") and "first_partial_ms" not in w:
            w["first_partial_ms"] = int((now - w["voice_at"]) * 1000)
            tl.info("turn @%s ms: first provisional text %d ms after voice", data.get("start_offset_ms"),
                    w["first_partial_ms"])
        elif data.get("final") and "commit_at" in w and "final_ms" not in w:
            w["final_ms"] = int((now - w["commit_at"]) * 1000)
            tl.info("turn @%s ms: final %d ms after commit (turn audio %s ms)", data.get("start_offset_ms"),
                    w["final_ms"], w.get("audio_ms"))

    def _on_chunk(self, chunk: AudioChunk) -> None:
        if self.uploader:
            self.uploader.submit(chunk)

    def ask(self, question: str, selected: str) -> None:
        if not self.meeting:
            self.win.set_status("Start or join a meeting first")
            return
        try:
            self.api.ask(self.meeting["id"], question, selected or None)
        except ApiError as exc:
            self.win.set_status(f"Ask failed: {exc}")

    def on_event(self, msg: dict) -> None:
        if self.meeting and msg.get("meeting_id") and msg.get("meeting_id") != self.meeting.get("id"):
            return  # stale event from a previous meeting's subscription
        kind, data = msg.get("type", ""), msg.get("data", {})
        fw = self.win.floating_win
        if kind == "session.snapshot":
            self.win.transcript.clear()
            if fw:
                fw.transcript.clear()
            for utt in data.get("utterances", []):
                self.win.transcript.add_utterance(utt)
                if fw:
                    fw.transcript.add_utterance(utt)
            for seg in data.get("segments", []):
                self.win.transcript.add_segment(seg)
                if fw:
                    fw.transcript.add_segment(seg)
            segs = data.get("segments", [])
            if segs:
                max_end = max(int(s.get("end_offset_ms", 0)) for s in segs)
                if not self.capture and max_end > self.next_offset_ms:
                    self.next_offset_ms = max_end   # continue the meeting timeline after existing history
            self.win.transcript.clear_partials()
            for p in data.get("partials", []):
                self.win.transcript.set_partial(p["item_id"], p["text"], p["start_offset_ms"], False)
            for q in data.get("questions", []):
                self.win.transcript.mark_question(q["segment_id"], q["text"])
                if fw:
                    fw.transcript.mark_question(q["segment_id"], q["text"])
            self.win.answers.load_snapshot(data.get("answers", []))
            if fw:
                fw.answers.load_snapshot(data.get("answers", []))
        elif kind == "transcript.utterance":
            self.win.transcript.add_utterance(data)
            if fw:
                fw.transcript.add_utterance(data)
        elif kind == "transcript.partial":
            final = bool(data.get("final"))
            self.win.transcript.set_partial(data["item_id"], data.get("text", ""), data.get("start_offset_ms", 0), final)
            if fw:
                fw.transcript.set_partial(data["item_id"], data.get("text", ""), data.get("start_offset_ms", 0), final)
            self._log_live_timing(data)
        elif kind == "transcript.segment":
            self.win.transcript.add_segment(data)
            if fw:
                fw.transcript.add_segment(data)
        elif kind == "question.detected":
            self.win.transcript.mark_question(data["segment_id"], data["text"])
            if fw:
                fw.transcript.mark_question(data["segment_id"], data["text"])
        elif kind == "meeting.documents":
            self.win.set_status("Meeting document selection updated")
        elif kind == "demo_prep.updated":
            self.win.set_status("Demo Prep regenerated")
        elif kind.startswith("answer."):
            self.win.answers.handle_event(kind, data)
            if fw:
                fw.answers.handle_event(kind, data)


def _attach_to_interactive_desktop() -> None:
    """Ensure GUI attaches to the user's interactive Windows desktop (Default) if spawned in an isolated runner desktop."""
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            h_default = user32.OpenDesktopW("Default", 0, False, 0x10000000)  # GENERIC_ALL
            if h_default:
                user32.SetThreadDesktop(h_default)
        except Exception as exc:
            logging.debug("Could not attach thread to Default desktop: %s", exc)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    _attach_to_interactive_desktop()
    app = QApplication(sys.argv)
    api = ApiClient()

    # Wait for the backend (it may still be starting when the desktop app is launched together with it),
    # then use the local development auth bypass if it is enabled. Never fall back to the sign-in
    # dialog just because the backend was not ready yet.
    import time
    from PySide6.QtWidgets import QMessageBox
    config: dict = {}
    while True:
        deadline = time.time() + 90
        reachable = False
        while time.time() < deadline:
            try:
                config = api._req("GET", "/auth/config") or {}
                reachable = True
                break
            except Exception:  # backend not up yet
                app.processEvents()
                time.sleep(1.0)
        if reachable:
            break
        box = QMessageBox(QMessageBox.Icon.Warning, "Meeting Assistant",
                          "The backend is not running yet (http://localhost:8000)." + chr(10) + chr(10) +
                          "Start the backend, then press Retry.",
                          QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Close)
        if box.exec() != QMessageBox.StandardButton.Retry:
            return 0
    dev_mode = config.get("dev_auth_bypass", False)

    workspace_id = None
    if dev_mode:
        log.info("Local development mode detected — bypassing login dialog")
        try:
            workspaces = api.workspaces()
            if workspaces:
                workspace_id = workspaces[0]["id"]
        except Exception as e:
            log.warning("Could not pre-fetch dev workspace: %s", e)
    else:
        # Standard login flow when bypass is disabled
        dlg = LoginDialog()
        while True:
            if dlg.exec() != LoginDialog.DialogCode.Accepted:
                return 0
            try:
                api.login(*dlg.credentials())
                workspaces = api.workspaces()
                if not workspaces:
                    raise ApiError("This account has no workspace")
                workspace_id = workspaces[0]["id"]
                break
            except ApiError as exc:
                dlg.show_error(str(exc))

    log.info("Creating MainWindow...")
    window = MainWindow(dev_mode=dev_mode)
    log.info("Creating Controller...")
    controller = Controller(api, window, workspace_id)  # noqa: F841
    window.api_client = api

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", "--join", dest="code", default=None, help="Meeting code to join automatically")
    parser.add_argument("--no-capture", dest="no_capture", action="store_true",
                        help="Deprecated: capture never auto-starts; kept so old launch commands still work")
    args, _ = parser.parse_known_args()

    latest_code = args.code
    if not latest_code and workspace_id:
        try:
            m_list = api._req("GET", f"/workspaces/{workspace_id}/meetings")
            live_meetings = [m for m in m_list if m.get("status") == "live"]
            if live_meetings:
                latest_code = live_meetings[0]["code"]
                window.join_input.setText(latest_code)
        except Exception as e:
            log.debug("Could not pre-populate live meeting code: %s", e)

    if args.code:
        log.info("Auto-joining meeting with existing context: %s", args.code)
        controller.join_and_start(args.code)

    log.info("Showing window (HWND: %s)...", int(window.winId()))
    window.show()
    window.raise_()
    window.activateWindow()
    log.info("Entering app.exec()...")
    code = app.exec()
    controller.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())

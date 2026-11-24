"""Live Screen panel: shown INSIDE the main Meeting Assistant window (same header, same footer, same answer panel).

It takes the place of the transcript pane while "📺 Live" is on. Questions typed in the footer, the 🖥 Screen button and
all answers keep working exactly as before, in the same session.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from screen_intelligence import capture

FPS_CHOICES = [("1 fps", 1000), ("2 fps", 500), ("5 fps", 200)]


class LiveScreenPanel(QFrame):
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LiveScreenPanel")
        self.setStyleSheet("QFrame#LiveScreenPanel{background:#ffffff;border:1px solid #cbd5e1;border-radius:7px;}")
        self._paused = False
        self._frames = 0

        title = QLabel("📺 Live Screen")
        title.setStyleSheet("font-weight:700;font-size:13px;color:#0f172a;")
        self.monitor = QComboBox()
        self.monitor.addItem("Auto", capture.AUTO)
        for key, label in capture.list_screens():
            self.monitor.addItem(label, key)
        self.monitor.setToolTip("Which monitor to show. Auto = another monitor than this app's, or this one if there is only one.")
        self.speed = QComboBox()
        for label, ms in FPS_CHOICES:
            self.speed.addItem(label, ms)
        self.speed.setCurrentIndex(1)
        self.speed.setToolTip("Preview speed: lower is lighter on the CPU.")
        self.pause_btn = QPushButton("⏸")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setToolTip("Pause / resume the live preview")
        self.close_btn = QPushButton("✕")
        self.close_btn.setToolTip("Close Live Screen and go back to the transcript")
        for b in (self.pause_btn, self.close_btn):
            b.setFixedWidth(34)
        self.status = QLabel("")
        self.status.setStyleSheet("color:#475569;font-size:11px;")

        bar = QHBoxLayout()
        bar.setSpacing(6)
        for w in (title, self.monitor, self.speed, self.pause_btn):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.close_btn)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status)

        self.preview = QLabel("Starting live preview…")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(200, 140)
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.preview.setStyleSheet("background:#0f172a;color:#94a3b8;border-radius:6px;")
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#92400e;background:#fef3c7;border-radius:4px;padding:3px 8px;font-size:11px;")
        self.hint.hide()
        tip = QLabel("Type an instruction in the box below (e.g. “solve in Java”) and press 🖥 Screen to analyze.")
        tip.setStyleSheet("color:#64748b;font-size:11px;")
        tip.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        lay.addLayout(bar)
        lay.addLayout(status_row)
        lay.addWidget(self.hint)
        lay.addWidget(self.preview, 1)
        lay.addWidget(tip)

        self._timer = QTimer(self)
        self._timer.setInterval(self.speed.currentData())
        self._timer.timeout.connect(self._tick)
        self.pause_btn.toggled.connect(self._on_pause)
        self.speed.currentIndexChanged.connect(lambda _i: self._timer.setInterval(self.speed.currentData()))
        self.monitor.currentIndexChanged.connect(lambda _i: self._tick())
        self.close_btn.clicked.connect(self.close_requested.emit)

    # ---- state ----------------------------------------------------------------------------
    def current_monitor(self) -> str:
        return self.monitor.currentData()

    def start(self) -> None:
        if not self._paused:
            self._timer.start()
        self._tick()

    def stop(self) -> None:  # no capturing while the panel is not shown
        self._timer.stop()

    def _on_pause(self, paused: bool) -> None:
        self._paused = paused
        self.pause_btn.setText("▶" if paused else "⏸")
        if paused:
            self._timer.stop()
            self.status.setText("Preview paused")
        else:
            self._timer.start()
            self.status.setText("")

    # ---- live preview ---------------------------------------------------------------------
    def _tick(self) -> None:
        win = self.window()
        if self._paused or not self.isVisible() or win.windowOpacity() < 1.0:
            return  # skip frames while a clean analysis capture hides the window
        try:
            screen = capture.pick_screen(self.monitor.currentData(), win.screen())
            pix = screen.grabWindow(0)
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Preview error: {exc}")
            return
        if pix.isNull():
            return
        self._frames += 1
        self.preview.setPixmap(pix.scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        same = screen is win.screen()
        self.hint.setVisible(same)
        if same:
            self.hint.setText("This preview shows the monitor Meeting Assistant is on, so the app appears inside itself. "
                              "Move the app to another monitor, or pick another screen. 🖥 Screen still captures a clean "
                              "screen (the app hides for a moment).")

    def set_status(self, text: str) -> None:
        self.status.setText(text if len(text) < 70 else text[:67] + "…")

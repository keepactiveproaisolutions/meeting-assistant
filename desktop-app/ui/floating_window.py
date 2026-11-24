"""Compact, resizable floating assistant window reusing existing meeting state."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from ui.answer_view import AnswerView
from ui.privacy import set_window_capture_exclusion
from ui.transcript_view import TranscriptView


class FloatingWindow(QMainWindow):
    send_clicked = Signal(str, str)     # question, selected transcript text
    restore_main = Signal()             # request switch back to main window

    def __init__(self, privacy_enabled: bool = False, always_on_top: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("Meeting Assistant (Floating)")
        self.resize(550, 480)
        self.setMinimumSize(380, 300)

        self._privacy_enabled = privacy_enabled
        self._always_on_top = always_on_top

        flags = Qt.WindowType.Window
        if always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

        # Top bar: meeting code indicator, Always on Top toggle, and Back button
        top_bar = QHBoxLayout()
        self.meeting_badge = QLabel("Meeting: —")
        self.meeting_badge.setStyleSheet("font-weight:bold;color:#1e40af;font-size:12px;")

        self.on_top_cb = QCheckBox("Always on top")
        self.on_top_cb.setChecked(always_on_top)
        self.on_top_cb.toggled.connect(self._toggle_always_on_top)

        self.back_btn = QPushButton("Main View")
        self.back_btn.setStyleSheet("padding:3px 8px;font-size:11px;")
        self.back_btn.clicked.connect(self.restore_main.emit)

        top_bar.addWidget(self.meeting_badge)
        top_bar.addStretch()
        top_bar.addWidget(self.on_top_cb)
        top_bar.addWidget(self.back_btn)

        # Content: Compact Transcript and Answers
        self.transcript = TranscriptView()
        self.answers = AnswerView()
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.transcript)
        split.addWidget(self.answers)
        split.setSizes([240, 200])

        # Bottom: Compact input and Send button
        bottom = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask a question...")
        self.input.returnPressed.connect(self._send)
        self.input.setStyleSheet("padding:6px;font-size:12px;")

        send_btn = QPushButton("Send")
        send_btn.setStyleSheet("background:#2563eb;color:#fff;font-weight:bold;padding:6px 14px;border-radius:4px;")
        send_btn.clicked.connect(self._send)

        bottom.addWidget(self.input, 1)
        bottom.addWidget(send_btn)

        # Root layout
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addLayout(top_bar)
        lay.addWidget(split, 1)
        lay.addLayout(bottom)
        self.setCentralWidget(root)

        self.transcript.ask_requested.connect(lambda q, _sid: self.send_clicked.emit(q, ""))
        self.transcript.use_requested.connect(lambda q, _sid: self._use_question(q))
        self.transcript.append_requested.connect(self._append_question)

    def set_privacy(self, enabled: bool) -> None:
        self._privacy_enabled = enabled
        if self.isVisible():
            hwnd = int(self.winId())
            set_window_capture_exclusion(hwnd, enabled)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Apply protection as soon as native window handle exists
        hwnd = int(self.winId())
        set_window_capture_exclusion(hwnd, self._privacy_enabled)

    def _toggle_always_on_top(self, checked: bool) -> None:
        self._always_on_top = checked
        was_visible = self.isVisible()
        flags = Qt.WindowType.Window
        if checked:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if was_visible:
            self.show()
            hwnd = int(self.winId())
            set_window_capture_exclusion(hwnd, self._privacy_enabled)

    def _use_question(self, text: str) -> None:
        """Card 'Use': put the exact question in the box for review/editing. Nothing is sent until Send."""
        self.input.setText(text.strip())
        self.input.setFocus()
        self.input.setCursorPosition(len(self.input.text()))

    def _append_question(self, text: str) -> None:
        cur = self.input.text().strip()
        self.input.setText(f"{cur} {text.strip()}".strip())
        self.input.setFocus()
        self.input.setCursorPosition(len(self.input.text()))

    def _send(self) -> None:
        typed = self.input.text().strip()
        selected = self.transcript.selected_text().strip()
        question = typed or selected
        if not question:
            return
        self.send_clicked.emit(question, selected if typed else "")
        self.input.clear()

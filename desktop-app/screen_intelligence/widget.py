"""The small "Screen" button for the composer bar: click = analyse the screen; the arrow picks the monitor."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import QMenu, QPushButton, QToolButton, QWidget

from screen_intelligence import capture


class LiveScreenButton(QPushButton):
    """Toggle: shows / hides the Live Screen panel inside the main window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("📺 Live", parent)
        self.setCheckable(True)
        self.setToolTip("Show a live preview of your screen inside this window (same header, footer and answers). "
                        "Click again to return to the transcript.")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self.setStyleSheet("QPushButton{background:#0f766e;color:#fff;font-weight:700;padding:0 10px;"
                           "border-radius:5px;border:none;font-size:13px;}QPushButton:hover{background:#115e59;}"
                           "QPushButton:checked{background:#134e4a;border:2px solid #5eead4;}")


class ScreenCopilotButton(QToolButton):
    analyze_requested = Signal(str)  # monitor key

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._monitor = capture.AUTO
        self.setText("🖥 Screen")
        self.setToolTip("AI Screen Intelligence: read my screen, find the question or coding problem and answer it "
                        "(Ctrl+Shift+S). Type an instruction first, e.g. “solve in Java”. The arrow picks the monitor.")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.setStyleSheet(
            "QToolButton{background:#7c3aed;color:#fff;font-weight:700;padding:0 10px;border-radius:5px;"
            "border:none;font-size:13px;}QToolButton:hover{background:#6d28d9;}"
            "QToolButton::menu-button{border:none;width:16px;}")
        self.clicked.connect(lambda: self.analyze_requested.emit(self._monitor))
        self._menu = QMenu(self)
        self._menu.aboutToShow.connect(self._build_menu)
        self.setMenu(self._menu)

    def _build_menu(self) -> None:
        self._menu.clear()
        group = QActionGroup(self._menu)
        choices = [(capture.AUTO, "Auto (the other monitor, or this one)")] + capture.list_screens()
        for key, label in choices:
            act = QAction(label, self._menu, checkable=True)
            act.setChecked(key == self._monitor)
            act.triggered.connect(lambda _c=False, k=key: setattr(self, "_monitor", k))
            group.addAction(act)
            self._menu.addAction(act)

    def attach_shortcut_to(self, window: QWidget) -> None:
        QShortcut(QKeySequence("Ctrl+Shift+S"), window).activated.connect(
            lambda: self.analyze_requested.emit(self._monitor))

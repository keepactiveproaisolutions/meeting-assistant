"""Main window: separates pre-meeting setup from a compact active-meeting toolbar so the
Live Transcript and AI Answers workspace gets 80–85%+ of the window height.
"""
from __future__ import annotations

import ipaddress
import socket

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from screen_intelligence.live_panel import LiveScreenPanel
from screen_intelligence.widget import LiveScreenButton, ScreenCopilotButton
from ui.answer_view import AnswerView
from ui.demo_prep_dialog import DemoPrepDialog
from ui.documents_dialog import DocumentsDialog
from ui.floating_window import FloatingWindow
from ui.privacy import (
    EXPLANATION_TEXT,
    STATUS_DISABLED,
    STATUS_UNSUPPORTED,
    WEB_LIMITATION_NOTE,
    is_display_affinity_supported,
    set_window_capture_exclusion,
)
from ui.qr import qr_pixmap
from ui.transcript_view import TranscriptView


def detect_lan_ip() -> str | None:
    """Detect the PC's active private LAN IPv4 address (e.g. 192.168.1.8) for phone QR joining."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            ip = sock.getsockname()[0]
        addr = ipaddress.ip_address(ip)
        if addr.is_private and not addr.is_loopback:
            return ip
    except Exception:  # noqa: BLE001
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            addr = ipaddress.ip_address(ip)
            if addr.is_private and not addr.is_loopback and not str(ip).startswith("169.254."):
                return ip
    except Exception:  # noqa: BLE001
        pass
    return None


def resolve_lan_join_url(code: str, raw_join_url: str = "") -> str:
    """Ensure the join URL and QR code always use the current PC LAN IP (never localhost/127.0.0.1)."""
    lan_ip = detect_lan_ip()
    clean_code = (code or "").strip().upper()
    if lan_ip and clean_code:
        return f"http://{lan_ip}:5173/join?code={clean_code}"
    if raw_join_url and lan_ip:
        return (
            raw_join_url.replace("localhost", lan_ip)
            .replace("127.0.0.1", lan_ip)
        )
    if raw_join_url:
        return raw_join_url
    return f"http://{lan_ip or 'localhost'}:5173/join?code={clean_code}" if clean_code else "Not connected"


class VerificationDialog(QDialog):
    """How to verify capture exclusion dialog."""

    def __init__(self, parent=None, privacy_enabled: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("How to Verify Screen Capture Exclusion")
        self.resize(520, 360)
        self._privacy_enabled = privacy_enabled

        layout = QVBoxLayout(self)

        title = QLabel("<b>Manual Verification Steps</b>")
        title.setStyleSheet("font-size:15px;color:#1e3a8a;")
        layout.addWidget(title)

        steps = QLabel(
            "<ol>"
            "<li>Open this assistant and enable <b>'Hide from screen capture'</b>.</li>"
            "<li>Start a test call in <b>Microsoft Teams, Zoom, or Google Meet</b> and share your <b>full desktop screen</b>.</li>"
            "<li>Check from a <b>second device</b> (e.g. mobile phone or another test participant's screen).</li>"
            "<li>Confirm whether this assistant window is: "
            "<ul>"
            "<li><b>Completely absent</b> (transparently excluded on Windows 10 v2004+ / Windows 11)</li>"
            "<li><b>A black rectangle</b> (older Windows versions / classic capture)</li>"
            "<li><b>Visible</b> (capture tool uses a method that bypasses DWM exclusion)</li>"
            "</ul></li>"
            "<li>Repeat this verification with any screen recording or streaming software you intend to use.</li>"
            "</ol>"
            "<p><i>Note: A successful Windows API call does not prove remote participants cannot see the window. "
            "Always verify with your specific sharing tool before sensitive meetings.</i></p>"
        )
        steps.setWordWrap(True)
        layout.addWidget(steps)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._privacy_enabled:
            hwnd = int(self.winId())
            set_window_capture_exclusion(hwnd, True)


class ShareMeetingDialog(QDialog):
    """Compact popover dialog for sharing the meeting code, LAN/Web link, and phone QR code."""

    def __init__(
        self,
        code: str,
        join_url: str,
        qr_lbl_pixmap,
        privacy_enabled: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Share Meeting & Mobile Companion ({code or '—'})")
        self.resize(420, 340)
        self._privacy_enabled = privacy_enabled
        self._code = code
        self._url = join_url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        hdr = QLabel("<b>Open Companion Web App on Phone or Browser</b>")
        hdr.setStyleSheet("font-size:14px;color:#1e3a8a;")
        lay.addWidget(hdr)

        code_row = QHBoxLayout()
        code_badge = QLabel(f"Room Code: {code or '—'}")
        code_badge.setStyleSheet(
            "font-family:Consolas, monospace;font-size:16px;font-weight:700;color:#1e40af;"
            "background:#dbeafe;padding:6px 12px;border-radius:6px;"
        )
        copy_code_btn = QPushButton("📋 Copy Code")
        copy_code_btn.setStyleSheet(
            "QPushButton{background:#eff6ff;color:#1d4ed8;border:1px solid #93c5fd;border-radius:5px;padding:6px 12px;font-weight:600;}"
        )
        copy_code_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._code))
        code_row.addWidget(code_badge)
        code_row.addStretch()
        code_row.addWidget(copy_code_btn)
        lay.addLayout(code_row)

        url_box = QLabel(join_url or "Not connected")
        url_box.setWordWrap(True)
        url_box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        url_box.setStyleSheet(
            "font-family:Consolas, monospace;font-size:12px;color:#334155;background:#f8fafc;"
            "border:1px solid #cbd5e1;border-radius:6px;padding:8px;"
        )
        lay.addWidget(url_box)

        copy_link_btn = QPushButton("🔗 Copy Companion Web Link")
        copy_link_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#ffffff;border:none;border-radius:5px;padding:7px 14px;font-weight:700;}"
        )
        copy_link_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._url))
        lay.addWidget(copy_link_btn)

        if qr_lbl_pixmap and not qr_lbl_pixmap.isNull():
            qr_holder = QHBoxLayout()
            qr_holder.addStretch()
            qr_view = QLabel()
            qr_view.setFixedSize(150, 150)
            qr_view.setScaledContents(True)
            qr_view.setPixmap(qr_lbl_pixmap)
            qr_view.setStyleSheet("background:#ffffff;border:1px solid #cbd5e1;border-radius:6px;padding:6px;")
            qr_holder.addWidget(qr_view)
            qr_holder.addStretch()
            lay.addLayout(qr_holder)

        note = QLabel(f"<i>{WEB_LIMITATION_NOTE}</i>")
        note.setWordWrap(True)
        note.setStyleSheet("font-size:11px;color:#b45309;")
        lay.addWidget(note)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._privacy_enabled:
            set_window_capture_exclusion(int(self.winId()), True)


class MainWindow(QMainWindow):
    create_clicked = Signal(str)      # meeting title
    join_clicked = Signal(str)        # meeting code
    toggle_capture_clicked = Signal()
    end_meeting_clicked = Signal()
    stop_clicked = Signal()
    send_clicked = Signal(str, str)   # question, selected transcript text
    screen_clicked = Signal(str, str)  # optional instruction, monitor key (AI Screen Intelligence)

    def __init__(self, dev_mode: bool = True) -> None:
        super().__init__()
        self.setWindowTitle("Meeting Assistant - Desktop")
        self.resize(1366, 768)
        self.setMinimumSize(780, 500)

        self.settings = QSettings("MeetingAssistant", "DesktopApp")
        self.api_client = None          # set by the controller; used by Documents / Demo Prep dialogs
        self.current_meeting_id: str | None = None
        # Default screen-capture exclusion ON (True) so the window is invisible to clients during screen share
        self._privacy_enabled = self.settings.value("privacy/hide_from_screen_capture", True, type=bool)
        if not self._privacy_enabled:
            self._privacy_enabled = True
            self.settings.setValue("privacy/hide_from_screen_capture", True)
        self._always_on_top = self.settings.value("window/always_on_top", False, type=bool)
        saved_ratio = self.settings.value("workspace/split_ratio", 0.45, type=float)
        self._split_ratio: float = max(0.22, min(0.78, float(saved_ratio or 0.45)))
        self._expanded_pane: str | None = None  # None | "transcript" | "answer"
        self._in_active_meeting: bool = False

        # Hidden attribute kept only if any test checks self.banner
        self.banner = QLabel("")
        self.banner.setVisible(False)

        # Shared underlying controls (preserved for Controller compatibility)
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Meeting title (optional)")
        self.title_input.setStyleSheet("padding:5px 8px;font-size:12.5px;border:1px solid #cbd5e1;border-radius:5px;")

        self.create_btn = QPushButton("Create Meeting")
        self.create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.create_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#fff;font-weight:700;padding:5px 12px;border-radius:5px;border:none;font-size:12.5px;}"
            "QPushButton:hover{background:#1d4ed8;}"
        )
        self.create_btn.clicked.connect(self._create)

        self.join_input = QLineEdit()
        self.join_input.setPlaceholderText("Code (e.g. SWL34JVU)")
        self.join_input.setMaximumWidth(165)
        self.join_input.setStyleSheet(
            "padding:5px 8px;font-family:Consolas, monospace;text-transform:uppercase;font-size:12.5px;border:1px solid #cbd5e1;border-radius:5px;"
        )

        self.join_btn = QPushButton("Join Meeting")
        self.join_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.join_btn.setStyleSheet(
            "QPushButton{background:#059669;color:#fff;font-weight:700;padding:5px 12px;border-radius:5px;border:none;font-size:12.5px;}"
            "QPushButton:hover{background:#047857;}"
        )
        self.join_btn.clicked.connect(self._join)

        self.device_combo = QComboBox()
        self.device_combo.setStyleSheet("padding:3px 8px;font-size:12px;border:1px solid #cbd5e1;border-radius:4px;")
        self.device_combo.setMinimumWidth(220)

        # Transcription mode. Standard = 2 s chunks to whisper-1 (proven fallback). Live = streaming Realtime
        # transcription with provisional text. Only one paid path ever runs; switch while capture is stopped.
        self.mode_combo = QComboBox()
        self.mode_combo.setStyleSheet("padding:3px 8px;font-size:12px;border:1px solid #cbd5e1;border-radius:4px;")
        self.mode_combo.addItem("Live (streaming)", "live")
        self.mode_combo.addItem("Standard (2 s chunks)", "standard")
        self.mode_combo.setToolTip("Live shows words as they are spoken (provisional, then finalized). "
                                   "Standard is the fallback. Change only while transcription is stopped.")
        _mode = self.settings.value("transcription/mode", "live")
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(_mode)))
        self.mode_combo.currentIndexChanged.connect(
            lambda _i: self.settings.setValue("transcription/mode", self.mode_combo.currentData()))

        self.privacy_cb = QCheckBox("Hide from capture")
        self.privacy_cb.setToolTip(EXPLANATION_TEXT)
        self.privacy_cb.setStyleSheet("font-weight:700;color:#0f172a;font-size:11.5px;")
        self.privacy_cb.setChecked(self._privacy_enabled)
        self.privacy_cb.toggled.connect(self._on_privacy_toggled)

        self.privacy_status_lbl = QLabel(STATUS_DISABLED)
        self.privacy_status_lbl.setStyleSheet("font-size:11px;color:#64748b;font-weight:600;border:none;")

        self.always_on_top_cb = QCheckBox("Always on top")
        self.always_on_top_cb.setStyleSheet("font-size:11.5px;color:#334155;")
        self.always_on_top_cb.setChecked(self._always_on_top)
        self.always_on_top_cb.toggled.connect(self._on_always_on_top_toggled)

        self.verify_btn = QPushButton("Verify Privacy")
        self.verify_btn.setStyleSheet(
            "QPushButton{font-size:11px;padding:3px 8px;background:#f1f5f9;color:#1e293b;border:1px solid #cbd5e1;border-radius:4px;}"
        )
        self.verify_btn.clicked.connect(self._show_how_to_verify)

        # Hidden/Offscreen labels kept for backwards-compatible attribute access
        self.url_label = QLabel("Not connected")
        self.qr_label = QLabel()
        self.qr_label.setFixedSize(110, 110)

        # =====================================================================
        # VIEW A: PRE-MEETING SETUP CARD (Shown ONLY before joining a meeting)
        # =====================================================================
        self.setup_card = QFrame()
        self.setup_card.setObjectName("SetupCard")
        self.setup_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setup_card.setStyleSheet(
            "QFrame#SetupCard{background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;}"
        )
        setup_lay = QVBoxLayout(self.setup_card)
        setup_lay.setContentsMargins(12, 8, 12, 8)
        setup_lay.setSpacing(6)

        setup_row1 = QHBoxLayout()
        setup_row1.setSpacing(8)
        brand_lbl = QLabel("<b>Meeting Setup</b>")
        brand_lbl.setStyleSheet("font-size:14px;color:#1e3a8a;border:none;")
        setup_row1.addWidget(brand_lbl)
        setup_row1.addSpacing(6)
        setup_row1.addWidget(self.title_input, 1)
        setup_row1.addWidget(self.create_btn)
        setup_row1.addSpacing(8)
        setup_row1.addWidget(self.join_input)
        setup_row1.addWidget(self.join_btn)

        self.setup_demo_prep_btn = QPushButton("🎯 Demo Prep")
        self.setup_demo_prep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setup_demo_prep_btn.setStyleSheet(
            "QPushButton{background:#fef3c7;color:#92400e;font-weight:700;padding:5px 10px;border-radius:5px;border:1px solid #fcd34d;font-size:12px;}"
        )
        self.setup_demo_prep_btn.clicked.connect(self._open_demo_prep)
        setup_row1.addWidget(self.setup_demo_prep_btn)

        self.setup_docs_btn = QPushButton("👤 Upload CV / Documents")
        self.setup_docs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setup_docs_btn.setStyleSheet(
            "QPushButton{background:#ede9fe;color:#5b21b6;font-weight:700;padding:5px 10px;border-radius:5px;border:1px solid #c4b5fd;font-size:12px;}"
        )
        self.setup_docs_btn.clicked.connect(self._open_documents)
        setup_row1.addWidget(self.setup_docs_btn)
        setup_lay.addLayout(setup_row1)

        setup_row2 = QHBoxLayout()
        setup_row2.setSpacing(8)
        dev_lbl = QLabel("<b>Speaker Loopback:</b>")
        dev_lbl.setStyleSheet("font-size:11.5px;color:#334155;border:none;")
        setup_row2.addWidget(dev_lbl)
        self.setup_device_holder = QHBoxLayout()
        self.setup_device_holder.setContentsMargins(0, 0, 0, 0)
        self.setup_device_holder.addWidget(self.device_combo)
        self.setup_device_holder.addWidget(self.mode_combo)
        setup_row2.addLayout(self.setup_device_holder, 1)
        setup_row2.addSpacing(8)

        self.setup_privacy_cb = QCheckBox("🛡️ Hide from screen capture")
        self.setup_privacy_cb.setStyleSheet("font-size:11.5px;font-weight:700;color:#0f172a;")
        self.setup_privacy_cb.setChecked(self._privacy_enabled)
        self.setup_privacy_cb.toggled.connect(self._on_privacy_toggled)
        setup_row2.addWidget(self.setup_privacy_cb)
        setup_row2.addWidget(self.verify_btn)
        setup_lay.addLayout(setup_row2)

        # =====================================================================
        # VIEW B: ACTIVE-MEETING COMPACT TOOLBAR (Shown after joining; ~44px)
        # =====================================================================
        self.active_toolbar = QFrame()
        self.active_toolbar.setObjectName("ActiveToolbar")
        self.active_toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.active_toolbar.setFixedHeight(44)
        self.active_toolbar.setStyleSheet(
            "QFrame#ActiveToolbar{background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;}"
        )
        tb_lay = QHBoxLayout(self.active_toolbar)
        tb_lay.setContentsMargins(8, 4, 8, 4)
        tb_lay.setSpacing(5)

        # 1. Room Code Badge + Copy + Share/QR
        self.code_label = QLabel("Code: —")
        self.code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.code_label.setMinimumWidth(116)
        self.code_label.setStyleSheet(
            "font-size:12px;font-weight:700;font-family:Consolas, monospace;color:#1e40af;"
            "background:#dbeafe;padding:3px 7px;border-radius:5px;border:none;"
        )
        tb_lay.addWidget(self.code_label)

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setToolTip("Copy room code")
        self.copy_btn.setMinimumWidth(46)
        self.copy_btn.setStyleSheet(
            "QPushButton{font-size:11px;font-weight:600;padding:3px 7px;background:#f1f5f9;color:#1e293b;border:1px solid #cbd5e1;border-radius:4px;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )
        self.copy_btn.clicked.connect(self._copy_code)
        tb_lay.addWidget(self.copy_btn)

        self.share_btn = QPushButton("🔗 Share")
        self.share_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.share_btn.setToolTip("Show companion web link and phone QR code")
        self.share_btn.setMinimumWidth(64)
        self.share_btn.setStyleSheet(
            "QPushButton{font-size:11px;font-weight:600;padding:3px 7px;background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;border-radius:4px;}"
            "QPushButton:hover{background:#dbeafe;}"
        )
        self.share_btn.clicked.connect(self._open_share_dialog)
        tb_lay.addWidget(self.share_btn)

        # Keep copy_link_btn attribute for compatibility
        self.copy_link_btn = QPushButton("Copy Link")
        self.copy_link_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.url_label.text()))

        # 2. Start / Stop Transcription + Audio Activity + Connection Status
        self.toggle_capture_btn = QPushButton("Stop Transcription")
        self.toggle_capture_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_capture_btn.setEnabled(False)
        self.toggle_capture_btn.setMinimumWidth(134)
        self.toggle_capture_btn.setStyleSheet(
            "QPushButton{background:#dc2626;color:#fff;font-weight:700;padding:4px 9px;border-radius:5px;border:none;font-size:11.5px;}"
        )
        self.toggle_capture_btn.clicked.connect(self.toggle_capture_clicked)
        tb_lay.addWidget(self.toggle_capture_btn)

        self.audio_meter_lbl = QLabel("⚪ Idle")
        self.audio_meter_lbl.setMinimumWidth(68)
        self.audio_meter_lbl.setStyleSheet("font-size:11px;color:#64748b;font-weight:600;border:none;")
        tb_lay.addWidget(self.audio_meter_lbl)

        self.status = QLabel("Idle")
        self.status.setMinimumWidth(40)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.status.setStyleSheet("font-size:11px;color:#475569;border:none;")
        tb_lay.addWidget(self.status, 1)

        # 3. Workspace Split Quick Controls (45/55 default, Expand Transcript, Expand Answer)
        self.split_reset_btn = QPushButton("45:55")
        self.split_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.split_reset_btn.setToolTip("Reset workspace to 45% Transcript / 55% AI Answer")
        self.split_reset_btn.setMinimumWidth(46)
        self.split_reset_btn.setStyleSheet(
            "QPushButton{font-size:11px;font-weight:600;padding:3px 6px;background:#f8fafc;color:#334155;border:1px solid #cbd5e1;border-radius:4px;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )
        self.split_reset_btn.clicked.connect(self.reset_split_ratio)
        tb_lay.addWidget(self.split_reset_btn)

        # 4. Screen Capture Privacy Quick Toggle & Status Pill
        self.toolbar_privacy_cb = QCheckBox("🛡️ Hide Capture")
        self.toolbar_privacy_cb.setToolTip("Exclude window from supported desktop screen captures")
        self.toolbar_privacy_cb.setMinimumWidth(118)
        self.toolbar_privacy_cb.setStyleSheet("font-size:11px;font-weight:700;color:#0f172a;")
        self.toolbar_privacy_cb.setChecked(self._privacy_enabled)
        self.toolbar_privacy_cb.toggled.connect(self._on_privacy_toggled)
        tb_lay.addWidget(self.toolbar_privacy_cb)

        self.toolbar_privacy_pill = QLabel("Visible")
        self.toolbar_privacy_pill.setVisible(False)

        # 5. Demo Prep, Floating View, Settings, End Meeting
        self.demo_prep_btn = QPushButton("🎯 Demo Prep")
        self.demo_prep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.demo_prep_btn.setToolTip("Demo preparation generated from your selected CV, JD and project documents")
        self.demo_prep_btn.setStyleSheet(
            "QPushButton{background:#fef3c7;color:#92400e;font-weight:700;padding:3px 7px;border-radius:4px;border:1px solid #fcd34d;font-size:11px;}"
            "QPushButton:hover{background:#fde68a;}"
        )
        self.demo_prep_btn.clicked.connect(self._open_demo_prep)
        tb_lay.addWidget(self.demo_prep_btn)

        self.docs_btn = QPushButton("👤 CV / Docs")
        self.docs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.docs_btn.setToolTip("Upload CV / Documents and choose which ones this meeting uses")
        self.docs_btn.setStyleSheet(
            "QPushButton{background:#ede9fe;color:#5b21b6;font-weight:700;padding:3px 7px;border-radius:4px;border:1px solid #c4b5fd;font-size:11px;}"
            "QPushButton:hover{background:#ddd6fe;}"
        )
        self.docs_btn.clicked.connect(self._open_documents)
        tb_lay.addWidget(self.docs_btn)

        self.floating_btn = QPushButton("🪟 Float")
        self.floating_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.floating_btn.setToolTip("Switch to compact floating companion window")
        self.floating_btn.setMinimumWidth(58)
        self.floating_btn.setStyleSheet(
            "QPushButton{background:#f1f5f9;color:#1e293b;font-weight:600;padding:3px 6px;border-radius:4px;border:1px solid #cbd5e1;font-size:11px;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )
        self.floating_btn.clicked.connect(self._open_floating_view)
        tb_lay.addWidget(self.floating_btn)

        self.settings_btn = QPushButton("⚙️ Settings")
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.setToolTip("Audio device, privacy details, and meeting switching")
        self.settings_btn.setMinimumWidth(76)
        self.settings_btn.setStyleSheet(
            "QPushButton{background:#f1f5f9;color:#1e293b;font-weight:600;padding:3px 7px;border-radius:4px;border:1px solid #cbd5e1;font-size:11px;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )
        self.settings_btn.clicked.connect(self._open_settings_dialog)
        tb_lay.addWidget(self.settings_btn)

        self.end_meeting_btn = QPushButton("End Meeting")
        self.end_meeting_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.end_meeting_btn.setEnabled(False)
        self.end_meeting_btn.setMinimumWidth(84)
        self.end_meeting_btn.setStyleSheet(
            "QPushButton{background:#475569;color:#fff;font-weight:700;padding:4px 8px;border-radius:5px;border:none;font-size:11px;}"
            "QPushButton:hover{background:#334155;}"
        )
        self.end_meeting_btn.clicked.connect(self.end_meeting_clicked)
        tb_lay.addWidget(self.end_meeting_btn)

        self.active_toolbar.setVisible(False)

        # =====================================================================
        # 3. MAIN WORKSPACE SPLITTER (Transcript Left 45% | AI Answers Right 55%)
        # =====================================================================
        self.transcript = TranscriptView()
        self.answers = AnswerView()
        self.transcript.setMinimumWidth(240)
        self.answers.setMinimumWidth(280)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("MainWorkspaceSplitter")
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(6)
        self.main_splitter.setStyleSheet(
            "QSplitter#MainWorkspaceSplitter::handle{background:#cbd5e1;border-radius:2px;margin:2px 1px;}"
            "QSplitter#MainWorkspaceSplitter::handle:hover{background:#2563eb;}"
        )
        # Left pane = transcript, or the Live Screen panel while "📺 Live" is on (same header, footer and answers).
        self.live_panel = LiveScreenPanel()
        self.live_panel.close_requested.connect(lambda: self.live_btn.setChecked(False))
        self.left_stack = QStackedWidget()
        self.left_stack.setMinimumWidth(240)
        self.left_stack.addWidget(self.transcript)
        self.left_stack.addWidget(self.live_panel)
        self.main_splitter.addWidget(self.left_stack)
        self.main_splitter.addWidget(self.answers)
        self.main_splitter.setStretchFactor(0, 45)
        self.main_splitter.setStretchFactor(1, 55)
        self.main_splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.main_splitter.splitterMoved.connect(self._on_splitter_moved)

        self.transcript.expand_requested.connect(self.toggle_expand_transcript)
        self.answers.expand_requested.connect(self.toggle_expand_answer)

        # =====================================================================
        # 4. COMPACT BOTTOM COMPOSER (~40px)
        # =====================================================================
        composer_frame = QFrame()
        composer_frame.setObjectName("ComposerBar")
        composer_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        composer_frame.setFixedHeight(42)
        composer_frame.setStyleSheet(
            "QFrame#ComposerBar{background:#ffffff;border:1px solid #cbd5e1;border-radius:7px;}"
        )
        bottom = QHBoxLayout(composer_frame)
        bottom.setContentsMargins(8, 4, 6, 4)
        bottom.setSpacing(8)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a question, or select transcript text and press Send…")
        self.input.returnPressed.connect(self._send)
        self.input.setStyleSheet("border:none;background:transparent;padding:4px 6px;font-size:13.5px;color:#0f172a;")

        send = QPushButton("Send")
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setFixedHeight(30)
        send.setStyleSheet(
            "QPushButton{background:#2563eb;color:#fff;font-weight:700;padding:0 18px;border-radius:5px;border:none;font-size:13px;}"
            "QPushButton:hover{background:#1d4ed8;}"
        )
        send.clicked.connect(self._send)

        self.screen_btn = ScreenCopilotButton()  # AI Screen Intelligence & Coding Copilot
        self.screen_btn.analyze_requested.connect(self._screen_analyze)
        self.screen_btn.attach_shortcut_to(self)

        bottom.addWidget(self.input, 1)
        self.live_btn = LiveScreenButton()  # shows the Live Screen panel inside this window
        self.live_btn.toggled.connect(self.set_live_screen)

        bottom.addWidget(self.live_btn)
        bottom.addWidget(self.screen_btn)
        bottom.addWidget(send)

        # =====================================================================
        # ROOT CENTRAL LAYOUT (Toolbar=0, Workspace Splitter=1, Composer=0)
        # =====================================================================
        root = QWidget()
        root.setStyleSheet("background:#f1f5f9;")
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(8, 6, 8, 6)
        content_layout.setSpacing(6)
        content_layout.addWidget(self.setup_card, 0)
        content_layout.addWidget(self.active_toolbar, 0)
        content_layout.addWidget(self.main_splitter, 1)
        content_layout.addWidget(composer_frame, 0)

        lay.addLayout(content_layout, 1)
        self.setCentralWidget(root)

        self.current_code = ""
        self.current_join_url = ""
        self.floating_win: FloatingWindow | None = None

        # Card direct Send: immediately asks question to AI (Option 1)
        self.transcript.ask_requested.connect(lambda q, _sid: self.send_clicked.emit(q, ""))
        # Card Use: puts question in composer box for editing before sending (Option 2)
        self.transcript.use_requested.connect(lambda q, _sid: self._use_question(q))
        self.transcript.append_requested.connect(self._append_question)

        if self._always_on_top:
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        QTimer.singleShot(0, self._apply_saved_split_sizes)

    # -------------------------------------------------------------------------
    # Splitter sizing, persistence, and Expand Transcript / Expand Answer
    # -------------------------------------------------------------------------
    def _apply_saved_split_sizes(self) -> None:
        total = max(600, self.main_splitter.width() or (self.width() - 20))
        if self._expanded_pane == "transcript":
            left = int(total * 0.72)
        elif self._expanded_pane == "answer":
            left = int(total * 0.28)
        else:
            left = int(total * self._split_ratio)
        right = max(240, total - left)
        self.main_splitter.setSizes([left, right])
        self.transcript.set_expanded_label(self._expanded_pane == "transcript")
        self.answers.set_expanded_label(self._expanded_pane == "answer")

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        sizes = self.main_splitter.sizes()
        total = sum(sizes)
        if total > 100:
            ratio = round(sizes[0] / float(total), 3)
            self._split_ratio = max(0.22, min(0.78, ratio))
            self._expanded_pane = None
            self.settings.setValue("workspace/split_ratio", self._split_ratio)
            self.transcript.set_expanded_label(False)
            self.answers.set_expanded_label(False)

    def reset_split_ratio(self) -> None:
        self._expanded_pane = None
        self._split_ratio = 0.45
        self.settings.setValue("workspace/split_ratio", 0.45)
        self._apply_saved_split_sizes()

    def toggle_expand_transcript(self) -> None:
        if self._expanded_pane == "transcript":
            self._expanded_pane = None
        else:
            self._expanded_pane = "transcript"
        self._apply_saved_split_sizes()

    def toggle_expand_answer(self) -> None:
        if self._expanded_pane == "answer":
            self._expanded_pane = None
        else:
            self._expanded_pane = "answer"
        self._apply_saved_split_sizes()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_saved_split_sizes()

    # -------------------------------------------------------------------------
    # Window & Privacy Management
    # -------------------------------------------------------------------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_display_affinity()
        self._apply_saved_split_sizes()

    def _apply_display_affinity(self) -> None:
        if not is_display_affinity_supported():
            self.privacy_status_lbl.setText(STATUS_UNSUPPORTED)
            self.toolbar_privacy_pill.setText("Unsupported")
            self.toolbar_privacy_pill.setStyleSheet(
                "font-size:10.5px;font-weight:700;color:#b45309;background:#fef3c7;padding:2px 6px;border-radius:4px;"
            )
            return

        hwnd = int(self.winId())
        success, status_text, _err = set_window_capture_exclusion(hwnd, self._privacy_enabled)
        self.privacy_status_lbl.setText(status_text)
        if success and self._privacy_enabled:
            self.privacy_status_lbl.setStyleSheet("font-size:11.5px;color:#16a34a;font-weight:700;")
            self.toolbar_privacy_pill.setText("Protected")
            self.toolbar_privacy_pill.setStyleSheet(
                "font-size:10.5px;font-weight:700;color:#15803d;background:#dcfce7;padding:2px 6px;border-radius:4px;"
            )
        elif not success:
            self.privacy_status_lbl.setStyleSheet("font-size:11.5px;color:#dc2626;font-weight:700;")
            self.toolbar_privacy_pill.setText("Failed")
            self.toolbar_privacy_pill.setStyleSheet(
                "font-size:10.5px;font-weight:700;color:#b91c1c;background:#fee2e2;padding:2px 6px;border-radius:4px;"
            )
        else:
            self.privacy_status_lbl.setStyleSheet("font-size:11.5px;color:#64748b;font-weight:600;")
            self.toolbar_privacy_pill.setText("Visible")
            self.toolbar_privacy_pill.setStyleSheet(
                "font-size:10.5px;font-weight:700;color:#64748b;background:#f1f5f9;padding:2px 6px;border-radius:4px;"
            )

        if self.floating_win:
            self.floating_win.set_privacy(self._privacy_enabled)

    def _on_privacy_toggled(self, checked: bool) -> None:
        self._privacy_enabled = checked
        self.settings.setValue("privacy/hide_from_screen_capture", checked)
        for cb in (self.privacy_cb, self.setup_privacy_cb, self.toolbar_privacy_cb):
            if cb.isChecked() != checked:
                cb.blockSignals(True)
                cb.setChecked(checked)
                cb.blockSignals(False)
        self._apply_display_affinity()

    def _on_always_on_top_toggled(self, checked: bool) -> None:
        self._always_on_top = checked
        self.settings.setValue("window/always_on_top", checked)
        was_visible = self.isVisible()
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if was_visible:
            self.show()
            self._apply_display_affinity()

    def _show_how_to_verify(self) -> None:
        dlg = VerificationDialog(self, privacy_enabled=self._privacy_enabled)
        dlg.exec()

    def _open_demo_prep(self) -> None:
        dlg = DemoPrepDialog(self.api_client, self.current_meeting_id, parent=self,
                             privacy_enabled=self._privacy_enabled)
        dlg.exec()

    def _open_documents(self) -> None:
        dlg = DocumentsDialog(self.api_client, self.current_meeting_id, parent=self,
                              privacy_enabled=self._privacy_enabled)
        dlg.exec()

    def _refresh_lan_qr(self) -> str:
        """Regenerate the LAN join URL and QR code using the current PC LAN IP and active meeting code."""
        self.current_join_url = resolve_lan_join_url(self.current_code, self.current_join_url)
        self.url_label.setText(self.current_join_url)
        if self.current_code:
            self.qr_label.setPixmap(qr_pixmap(self.current_join_url))
        return self.current_join_url

    def _open_share_dialog(self) -> None:
        self._refresh_lan_qr()
        dlg = ShareMeetingDialog(
            code=self.current_code,
            join_url=self.current_join_url,
            qr_lbl_pixmap=self.qr_label.pixmap(),
            privacy_enabled=self._privacy_enabled,
            parent=self,
        )
        dlg.exec()

    def _open_settings_dialog(self) -> None:
        self._refresh_lan_qr()
        dlg = QDialog(self)
        dlg.setWindowTitle("Settings, Mobile QR Join & Screen Capture Privacy")
        dlg.resize(580, 520)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        # 0. Mobile Companion QR Code & Wi-Fi Join Link (Requested in Settings!)
        qr_hdr = QLabel("<b>1. Mobile Phone QR Code & Home Wi-Fi Join Link</b>")
        qr_hdr.setStyleSheet("font-size:13.5px;color:#1e3a8a;")
        lay.addWidget(qr_hdr)

        qr_card = QFrame()
        qr_card.setObjectName("SettingsQrCard")
        qr_card.setStyleSheet(
            "QFrame#SettingsQrCard{background:#f8fafc;border:1px solid #cbd5e1;border-radius:8px;}"
        )
        qr_row = QHBoxLayout(qr_card)
        qr_row.setContentsMargins(12, 10, 12, 10)
        qr_row.setSpacing(14)

        settings_qr_img = QLabel()
        settings_qr_img.setFixedSize(136, 136)
        settings_qr_img.setScaledContents(True)
        settings_qr_img.setStyleSheet("background:#ffffff;border:1px solid #cbd5e1;border-radius:6px;padding:4px;")
        if self.qr_label.pixmap() and not self.qr_label.pixmap().isNull():
            settings_qr_img.setPixmap(self.qr_label.pixmap())
        qr_row.addWidget(settings_qr_img)

        qr_info_col = QVBoxLayout()
        qr_info_col.setSpacing(6)
        code_lbl = QLabel(
            f"Active Meeting Code: <b style='font-family:Consolas,monospace;color:#1e3a8a;font-size:14px;'>"
            f"{self.current_code or 'None'}</b>"
        )
        code_lbl.setStyleSheet("font-size:12.5px;color:#0f172a;border:none;")
        qr_info_col.addWidget(code_lbl)

        url_box = QLineEdit(self.current_join_url or "Not connected")
        url_box.setReadOnly(True)
        url_box.setStyleSheet(
            "padding:5px 8px;background:#ffffff;border:1px solid #cbd5e1;border-radius:5px;font-size:12px;color:#0f172a;"
        )
        qr_info_col.addWidget(url_box)

        qr_btns = QHBoxLayout()
        copy_link_btn = QPushButton("📋 Copy Join Link")
        copy_link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_link_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#ffffff;font-weight:700;padding:5px 10px;border-radius:5px;border:none;font-size:11.5px;}"
        )
        copy_link_btn.clicked.connect(
            lambda: (
                QGuiApplication.clipboard().setText(url_box.text()),
                copy_link_btn.setText("✓ Copied Link"),
            )
        )

        refresh_qr_btn = QPushButton("🔄 Refresh LAN IP / QR")
        refresh_qr_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_qr_btn.setStyleSheet(
            "QPushButton{background:#f1f5f9;color:#0f172a;font-weight:600;padding:5px 10px;border-radius:5px;border:1px solid #cbd5e1;font-size:11.5px;}"
        )

        def _do_refresh_in_dlg() -> None:
            new_url = self._refresh_lan_qr()
            url_box.setText(new_url)
            if self.qr_label.pixmap():
                settings_qr_img.setPixmap(self.qr_label.pixmap())

        refresh_qr_btn.clicked.connect(_do_refresh_in_dlg)
        qr_btns.addWidget(copy_link_btn)
        qr_btns.addWidget(refresh_qr_btn)
        qr_btns.addStretch()
        qr_info_col.addLayout(qr_btns)

        qr_hint = QLabel(
            "Scan with your phone camera on the same Wi-Fi to open the synchronized mobile web companion."
        )
        qr_hint.setWordWrap(True)
        qr_hint.setStyleSheet("font-size:11px;color:#475569;border:none;")
        qr_info_col.addWidget(qr_hint)
        qr_row.addLayout(qr_info_col, 1)
        lay.addWidget(qr_card)

        # 1. Audio Output Device section
        audio_hdr = QLabel("<b>2. System Audio Capture (WASAPI Speaker Loopback Only)</b>")
        audio_hdr.setStyleSheet("font-size:13.5px;color:#1e3a8a;")
        lay.addWidget(audio_hdr)

        dev_note = QLabel(
            "Captures system output audio only (never the microphone). Stop transcription before changing devices."
        )
        dev_note.setWordWrap(True)
        dev_note.setStyleSheet("font-size:11.5px;color:#475569;")
        lay.addWidget(dev_note)

        # Temporarily place self.device_combo in the dialog while open
        lay.addWidget(self.device_combo)
        lay.addWidget(QLabel("Transcription mode:"))
        lay.addWidget(self.mode_combo)

        # 2. Screen Capture Exclusion & Window Options
        priv_hdr = QLabel("<b>3. Screen Capture Privacy & Window Behavior</b>")
        priv_hdr.setStyleSheet("font-size:13.5px;color:#1e3a8a;margin-top:4px;")
        lay.addWidget(priv_hdr)

        priv_row = QHBoxLayout()
        priv_row.addWidget(self.privacy_cb)
        priv_row.addWidget(self.privacy_status_lbl)
        priv_row.addStretch()
        priv_row.addWidget(self.always_on_top_cb)
        priv_row.addWidget(self.verify_btn)
        lay.addLayout(priv_row)

        expl = QLabel(EXPLANATION_TEXT)
        expl.setWordWrap(True)
        expl.setStyleSheet("font-size:11.5px;color:#475569;")
        lay.addWidget(expl)

        web_warn = QLabel(f"<i>{WEB_LIMITATION_NOTE}</i>")
        web_warn.setWordWrap(True)
        web_warn.setStyleSheet("font-size:11.5px;color:#b45309;")
        lay.addWidget(web_warn)

        # 3. Switch / Join Another Meeting
        switch_hdr = QLabel("<b>4. Switch or Create Another Meeting</b>")
        switch_hdr.setStyleSheet("font-size:13.5px;color:#1e3a8a;margin-top:4px;")
        lay.addWidget(switch_hdr)

        switch_row = QHBoxLayout()
        show_setup_btn = QPushButton("Show Meeting Setup Bar")
        show_setup_btn.setStyleSheet(
            "QPushButton{background:#f1f5f9;color:#0f172a;border:1px solid #cbd5e1;border-radius:5px;padding:5px 12px;font-weight:600;}"
        )
        show_setup_btn.clicked.connect(lambda: (self.setup_card.setVisible(not self.setup_card.isVisible()), dlg.accept()))
        switch_row.addWidget(show_setup_btn)
        switch_row.addStretch()

        close_btn = QPushButton("Done")
        close_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#ffffff;border:none;border-radius:5px;padding:6px 16px;font-weight:700;}"
        )
        close_btn.clicked.connect(dlg.accept)
        switch_row.addWidget(close_btn)
        lay.addLayout(switch_row)

        if self._privacy_enabled:
            set_window_capture_exclusion(int(dlg.winId()), True)

        dlg.exec()
        # Return device_combo to the setup_card layout
        self.setup_device_holder.addWidget(self.device_combo)
        self.setup_device_holder.addWidget(self.mode_combo)

    def _open_floating_view(self) -> None:
        if not self.floating_win:
            self.floating_win = FloatingWindow(
                privacy_enabled=self._privacy_enabled,
                always_on_top=self._always_on_top,
            )
            self.floating_win.restore_main.connect(self._restore_from_floating)
            self.floating_win.send_clicked.connect(self.send_clicked.emit)

        if self.current_code:
            self.floating_win.meeting_badge.setText(f"Meeting: {self.current_code}")

        self.hide()
        self.floating_win.show()

    def _restore_from_floating(self) -> None:
        if self.floating_win:
            self.floating_win.hide()
        self.show()
        self._apply_display_affinity()

    # -------------------------------------------------------------------------
    # Controller State Transitions
    # -------------------------------------------------------------------------
    def show_meeting(self, code: str, join_url: str) -> None:
        self.current_code = code
        self.current_join_url = resolve_lan_join_url(code, join_url)
        self.code_label.setText(f"Code: {code}")
        self.url_label.setText(self.current_join_url)
        self.qr_label.setPixmap(qr_pixmap(self.current_join_url))
        self._in_active_meeting = True
        self.setup_card.setVisible(False)
        self.active_toolbar.setVisible(True)
        if self.floating_win:
            self.floating_win.meeting_badge.setText(f"Meeting: {code}")
        QTimer.singleShot(10, self._apply_saved_split_sizes)

    def set_running(self, running: bool) -> None:
        self._in_active_meeting = running
        self.setup_card.setVisible(not running)
        self.active_toolbar.setVisible(running)
        self.create_btn.setEnabled(not running)
        self.join_btn.setEnabled(not running)
        self.title_input.setEnabled(not running)
        self.join_input.setEnabled(not running)
        self.device_combo.setEnabled(not running)
        self.toggle_capture_btn.setEnabled(running)
        self.end_meeting_btn.setEnabled(running)
        if not running:
            self.toggle_capture_btn.setText("Stop Transcription")
            self.toggle_capture_btn.setStyleSheet(
                "QPushButton{background:#dc2626;color:#fff;font-weight:700;padding:4px 11px;border-radius:5px;border:none;font-size:12px;}"
            )
        QTimer.singleShot(10, self._apply_saved_split_sizes)

    def set_capturing(self, capturing: bool, finishing: bool = False) -> None:
        self.device_combo.setEnabled(not capturing and not finishing)
        self.mode_combo.setEnabled(not capturing and not finishing)
        if finishing:
            self.toggle_capture_btn.setText("Finishing...")
            self.toggle_capture_btn.setEnabled(False)
            self.toggle_capture_btn.setStyleSheet(
                "QPushButton{background:#94a3b8;color:#fff;font-weight:700;padding:4px 11px;border-radius:5px;border:none;font-size:12px;}"
            )
        elif capturing:
            self.toggle_capture_btn.setText("Stop Transcription")
            self.toggle_capture_btn.setEnabled(True)
            self.toggle_capture_btn.setStyleSheet(
                "QPushButton{background:#dc2626;color:#fff;font-weight:700;padding:4px 11px;border-radius:5px;border:none;font-size:12px;}"
                "QPushButton:hover{background:#b91c1c;}"
            )
        else:
            self.toggle_capture_btn.setText("Start Transcription")
            self.toggle_capture_btn.setEnabled(True)
            self.toggle_capture_btn.setStyleSheet(
                "QPushButton{background:#059669;color:#fff;font-weight:700;padding:4px 11px;border-radius:5px;border:none;font-size:12px;}"
                "QPushButton:hover{background:#047857;}"
            )

    def populate_devices(self, devices: list[dict], selected_idx=None) -> None:
        """Device picker keyed by stable Windows endpoint ID (never by PortAudio index or name alone)."""
        from PySide6.QtGui import QStandardItemModel  # noqa: F401  (combo uses a standard item model)
        if not getattr(self, "_device_save_hooked", False):
            # Persist only when the USER picks a device — a temporary fallback must not overwrite their choice.
            def _picked(_i):
                data = self.device_combo.currentData() or "default"
                self.settings.setValue("audio/endpoint_id", data)
                logging.getLogger("ui.device").info("User picked device: %s (%s)", self.device_combo.currentText(), data)

            import logging
            self.device_combo.activated.connect(_picked)
            self.device_combo.currentIndexChanged.connect(
                lambda i: logging.getLogger("ui.device").info("Picker index -> %s: %s", i, self.device_combo.currentText()))
            self._device_save_hooked = True
        import logging
        logging.getLogger("ui.device").info("Rebuilding device list (keep selection: %s)", selected_idx or "saved")
        saved = (selected_idx if isinstance(selected_idx, str) and selected_idx else
                 self.settings.value("audio/endpoint_id", "default", type=str) or "default")
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        current = next((d["name"] for d in devices if d.get("is_default")), "none")
        self.device_combo.addItem(f"System default — follows Windows output (now: {current})", userData="default")
        match = 0
        for d in devices:
            label = d["name"] + (" (Windows default)" if d.get("is_default") else "")
            if d.get("ambiguous"):
                label += " — duplicate name, cannot be selected safely"
            self.device_combo.addItem(label, userData=d["endpoint_id"])
            row = self.device_combo.count() - 1
            if d.get("ambiguous"):
                item = self.device_combo.model().item(row)
                if item is not None:
                    item.setEnabled(False)
            elif d["endpoint_id"] == saved:
                match = row
        self.device_combo.setCurrentIndex(match)
        if saved != "default" and match == 0:
            self.set_status("Your saved playback device is not connected — using System default until you choose again.")
        self.device_combo.blockSignals(False)

    def get_mode(self) -> str:
        return self.mode_combo.currentData() or "standard"

    def set_mode(self, mode: str) -> None:
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(mode)))

    def get_selected_device(self) -> str:
        """'default' (follow Windows default output) or a stable endpoint ID."""
        return self.device_combo.currentData() or "default"

    def get_selected_device_index(self):  # backward-compatible alias
        return self.get_selected_device()

    def set_audio_activity(self, active: bool, rms: float) -> None:
        if active:
            self.audio_meter_lbl.setText(f"🟢 Audio ({int(rms)})")
            self.audio_meter_lbl.setStyleSheet("font-size:11px;color:#16a34a;font-weight:700;border:none;")
        else:
            self.audio_meter_lbl.setText("⚪ Waiting for audio")
            self.audio_meter_lbl.setStyleSheet("font-size:11px;color:#64748b;border:none;")

    def set_status(self, text: str) -> None:
        import logging
        import threading
        names = [t.name for t in threading.enumerate()]
        logging.getLogger("ui.status").info(
            "UI status: %s | watchdogs=%d uploaders=%d", text,
            names.count("loopback-watchdog"), names.count("chunk-uploader"))
        self.status.setText(text)
        self.status.setToolTip(text)
        lower = (text or "").lower()
        # Capture state also goes on the audio indicator, which realtime/network messages never overwrite.
        if lower.startswith("capture paused"):
            self.audio_meter_lbl.setText("⏸ Paused — device disconnected")
            self.audio_meter_lbl.setStyleSheet("font-size:11px;color:#b91c1c;font-weight:700;border:none;")
        elif lower.startswith(("capture resumed", "capture switched")):
            self.audio_meter_lbl.setText("▶ Resumed")
            self.audio_meter_lbl.setStyleSheet("font-size:11px;color:#16a34a;font-weight:700;border:none;")
        if lower.startswith(("capture resumed", "capture switched")):
            self.status.setStyleSheet(
                "font-size:11px;color:#065f46;background:#d1fae5;padding:2px 6px;border-radius:4px;font-weight:600;"
            )
        elif "error" in lower or "failed" in lower or "disconnected" in lower or "paused" in lower:
            self.status.setStyleSheet(
                "font-size:11px;color:#b91c1c;background:#fee2e2;padding:2px 6px;border-radius:4px;font-weight:700;"
            )
        elif "connected" in lower or "capturing" in lower:
            self.status.setStyleSheet(
                "font-size:11px;color:#065f46;background:#d1fae5;padding:2px 6px;border-radius:4px;font-weight:600;"
            )
        else:
            self.status.setStyleSheet("font-size:11px;color:#475569;border:none;")

    def _create(self) -> None:
        title = self.title_input.text().strip() or "Live Meeting"
        self.create_clicked.emit(title)

    def _join(self) -> None:
        code = self.join_input.text().strip().upper()
        if code:
            self.join_clicked.emit(code)

    def _copy_code(self) -> None:
        if self.current_code:
            QGuiApplication.clipboard().setText(self.current_code)
            self.copy_btn.setText("✓ Copied")
            QTimer.singleShot(1400, lambda: self.copy_btn.setText("Copy"))

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

    def set_live_screen(self, on: bool) -> None:
        """Swap the left pane between the transcript and the live screen preview (header/footer unchanged)."""
        if self.live_btn.isChecked() != on:
            self.live_btn.setChecked(on)
            return
        self.left_stack.setCurrentWidget(self.live_panel if on else self.transcript)
        if on:
            self.live_panel.start()
        else:
            self.live_panel.stop()

    def _screen_analyze(self, monitor: str) -> None:
        note = self.input.text().strip()  # optional instruction typed in the box, e.g. "solve in Java"
        if self.live_btn.isChecked():  # use the screen shown in the live panel
            monitor = self.live_panel.current_monitor()
        self.screen_clicked.emit(note, monitor)
        self.input.clear()

    def _send(self) -> None:
        typed = self.input.text().strip()
        selected = self.transcript.selected_text().strip()
        question = typed or selected
        if not question:
            return
        self.send_clicked.emit(question, selected if typed else "")
        self.input.clear()

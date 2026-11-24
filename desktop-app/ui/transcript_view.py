"""Live transcript view: Wispr Flow style continuous paragraphs with raw chunk fallback,
context-aware question detection, and clean copying.
"""
import html

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def fmt_ts(ms: int) -> str:
    s = int(ms) // 1000
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class ParagraphRow(QFrame):
    """A continuous conversational speech paragraph / utterance."""

    ask_requested = Signal(str, str)     # direct instant send to AI
    use_requested = Signal(str, str)     # copy to composer box for editing
    append_requested = Signal(str)       # append card text to composer box

    def __init__(self, utterance: dict) -> None:
        super().__init__()
        self.utterance_id = utterance["id"]
        self.offset_ms = utterance["start_offset_ms"]
        self.end_ms = utterance.get("end_offset_ms", self.offset_ms)
        self.text = utterance["text"]
        self.questions = utterance.get("questions", [])
        self.is_final = utterance.get("is_final", False)
        self._tail = ""  # Live provisional words after the finalized text (display only; never sent/copied)

        self.setObjectName("ParagraphRow")
        self._apply_normal_style()

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(10, 8, 10, 8)
        main_lay.setSpacing(4)

        # Header: Timestamp + Actions
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        self.ts_lbl = QLabel(fmt_ts(self.offset_ms))
        self.ts_lbl.setStyleSheet(
            "color:#64748b; font-family:Consolas, monospace; font-size:11.5px; font-weight:bold; border:none; background:transparent;"
        )

        self.status_indicator = QLabel("● live" if not self.is_final else "")
        self.status_indicator.setStyleSheet(
            "color:#10b981; font-size:11px; font-style:italic; border:none; background:transparent;"
        )

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setFixedSize(50, 22)
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setStyleSheet(
            "QPushButton{font-size:11px; padding:1px 6px; background:#f1f5f9; color:#334155; border:1px solid #cbd5e1; border-radius:4px;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )
        self.copy_btn.clicked.connect(self._copy_paragraph)

        self.ask_btn = QPushButton("Send")
        self.ask_btn.setFixedSize(52, 22)
        self.ask_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ask_btn.setToolTip("Send this card directly to AI to generate an answer")

        if bool(self.questions or utterance.get("is_question")):
            self.ask_btn.setStyleSheet(
                "QPushButton:disabled{background:#cbd5e1; color:#f8fafc;}"
                "QPushButton{font-size:11px; font-weight:bold; padding:1px 8px; background:#f59e0b; color:#ffffff; border:none; border-radius:4px;}"
                "QPushButton:hover{background:#d97706;}"
            )
        else:
            self.ask_btn.setStyleSheet(
                "QPushButton:disabled{background:#cbd5e1; color:#f8fafc;}"
                "QPushButton{font-size:11px; font-weight:bold; padding:1px 8px; background:#2563eb; color:#ffffff; border:none; border-radius:4px;}"
                "QPushButton:hover{background:#1d4ed8;}"
            )
        self.ask_btn.clicked.connect(self._on_ask)

        top_row.addWidget(self.ts_lbl)
        top_row.addWidget(self.status_indicator)
        top_row.addStretch()
        top_row.addWidget(self.ask_btn)
        top_row.addWidget(self.copy_btn)
        main_lay.addLayout(top_row)

        # Body: Flowing text with normal visual wrapping
        self.body = QLabel(self.text)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.setStyleSheet(
            "font-size:13.5px; line-height:1.5; color:#0f172a; padding: 1px 0; border:none; background:transparent;"
        )
        main_lay.addWidget(self.body)
        self.ask_btn.setEnabled(bool(self.text.strip()))

        if self.questions or utterance.get("is_question"):
            self._apply_question_style()

    def _render_body(self) -> None:
        if self._tail:
            final = html.escape(self.text)
            sep = " " if final else ""
            self.body.setTextFormat(Qt.TextFormat.RichText)
            self.body.setText(f'{final}{sep}<span style="color:#0f172a; background-color:#fef9c3;">{html.escape(self._tail)}</span>')
        else:
            self.body.setTextFormat(Qt.TextFormat.PlainText)
            self.body.setText(self.text)
        self.status_indicator.setText("● live" if (self._tail or not self.is_final) else "")
        # Send works as soon as words appear: finalized text plus the live words still being spoken.
        if self.ask_btn.text() == "Send":
            self.ask_btn.setEnabled(bool(self._sendable()))
        self.copy_btn.setEnabled(bool(self._sendable()))

    def _sendable(self) -> str:
        return (f"{self.text.strip()} {self._tail}").strip()

    def set_tail(self, tail: str) -> None:
        tail = (tail or "").strip()
        if tail != self._tail:
            self._tail = tail
            self._render_body()

    @property
    def tail(self) -> str:
        return self._tail

    def _apply_normal_style(self) -> None:
        self.setStyleSheet(
            "QFrame#ParagraphRow {"
            "  background: #ffffff;"
            "  border: 1px solid #e2e8f0;"
            "  border-radius: 7px;"
            "}"
        )

    def _apply_question_style(self) -> None:
        self.setStyleSheet(
            "QFrame#ParagraphRow {"
            "  background: #fffbeb;"
            "  border: 1px solid #fde68a;"
            "  border-left: 4px solid #f59e0b;"
            "  border-radius: 7px;"
            "}"
        )

    def update_utterance(self, utterance: dict) -> None:
        self.text = utterance["text"]
        self.end_ms = utterance.get("end_offset_ms", self.end_ms)
        self.is_final = utterance.get("is_final", False)
        self.questions = utterance.get("questions", self.questions)

        self._render_body()

        has_q = bool(self.questions or utterance.get("is_question"))
        if has_q:
            self.ask_btn.setStyleSheet(
                "QPushButton:disabled{background:#cbd5e1; color:#f8fafc;}"
                "QPushButton{font-size:11px; font-weight:bold; padding:1px 6px; background:#f59e0b; color:#ffffff; border:none; border-radius:4px;}"
            )
            self._apply_question_style()
        else:
            self.ask_btn.setStyleSheet(
                "QPushButton:disabled{background:#cbd5e1; color:#f8fafc;}"
                "QPushButton{font-size:11px; font-weight:bold; padding:1px 6px; background:#2563eb; color:#ffffff; border:none; border-radius:4px;}"
            )
            self._apply_normal_style()

    def _on_ask(self) -> None:
        # Submit the entire visible paragraph text from this card, not just the first extracted question
        full_paragraph = self._sendable()   # finalized text + the live words heard so far
        if not full_paragraph:
            return
        self.ask_btn.setEnabled(False)
        self.ask_btn.setText("Sent")
        QTimer.singleShot(1500, lambda: (self.ask_btn.setText("Send"), self.ask_btn.setEnabled(bool(self._sendable()))))
        self.ask_requested.emit(full_paragraph, self.utterance_id)

    def _on_use(self) -> None:
        full_paragraph = self._sendable()
        self.use_requested.emit(full_paragraph, self.utterance_id)

    def _copy_paragraph(self) -> None:
        QGuiApplication.clipboard().setText(f"[{fmt_ts(self.offset_ms)}] {self._sendable()}")

    def selected_text(self) -> str:
        return self.body.selectedText()


class RawChunkRow(QFrame):
    """Fallback debug row displaying raw 2s transport chunks."""

    def __init__(self, segment: dict) -> None:
        super().__init__()
        self.segment_id = segment["id"]
        self.offset_ms = segment["start_offset_ms"]
        self.text = segment["text"]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        ts = QLabel(fmt_ts(self.offset_ms))
        ts.setStyleSheet("color:#94a3b8; font-family:Consolas, monospace; font-size:11px; border:none;")
        body = QLabel(self.text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet("font-size:12px; color:#475569; border:none;")
        lay.addWidget(ts, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(body, 1)


class TranscriptView(QWidget):
    ask_requested = Signal(str, str)
    use_requested = Signal(str, str)
    append_requested = Signal(str)
    expand_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._paragraphs: dict[str, ParagraphRow] = {}
        self._raw_chunks: dict[str, RawChunkRow] = {}
        self._current_mode = "paragraphs"

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(4)

        # Header controls
        header = QHBoxLayout()
        header.setContentsMargins(2, 2, 2, 2)
        header.setSpacing(4)
        title = QLabel("<b>Transcript</b>")
        title.setStyleSheet("font-size:13px; color:#0f172a; border:none;")
        header.addWidget(title)

        self.rb_paragraphs = QRadioButton("Flow")
        self.rb_paragraphs.setToolTip("Continuous speech paragraphs (Wispr Flow style)")
        self.rb_paragraphs.setChecked(True)
        self.rb_paragraphs.setStyleSheet("font-size:11px; color:#334155;")
        self.rb_raw = QRadioButton("Raw")
        self.rb_raw.setToolTip("Raw 2-second audio chunks")
        self.rb_raw.setStyleSheet("font-size:11px; color:#334155;")

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.rb_paragraphs, 1)
        self.mode_group.addButton(self.rb_raw, 2)
        self.mode_group.idClicked.connect(self._on_mode_switched)

        header.addWidget(self.rb_paragraphs)
        header.addWidget(self.rb_raw)
        header.addStretch()

        copy_clean = QPushButton("Copy Text")
        copy_clean.setToolTip("Copy pure text without timestamps")
        copy_clean.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_clean.setStyleSheet(
            "QPushButton{font-size:10.5px; padding:2px 6px; background:#f1f5f9; color:#1e293b; border:1px solid #cbd5e1; border-radius:4px;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )
        copy_clean.clicked.connect(self.copy_clean_text)

        copy_all = QPushButton("Copy All")
        copy_all.setToolTip("Copy full transcript with timestamps")
        copy_all.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_all.setStyleSheet(
            "QPushButton{font-size:10.5px; font-weight:bold; padding:2px 6px; background:#2563eb; color:#fff; border:none; border-radius:4px;}"
        )
        copy_all.clicked.connect(self.copy_all)

        self.expand_btn = QPushButton("◧ Expand")
        self.expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_btn.setToolTip("Expand Live Transcript panel or restore balanced split")
        self.expand_btn.setStyleSheet(
            "QPushButton{font-size:10.5px; font-weight:600; padding:2px 6px; background:#f1f5f9; color:#1e293b; border:1px solid #cbd5e1; border-radius:4px;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )
        self.expand_btn.clicked.connect(self.expand_requested.emit)

        for b in (copy_clean, copy_all, self.expand_btn):
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        header.addWidget(copy_clean)
        header.addWidget(copy_all)
        header.addWidget(self.expand_btn)
        root_lay.addLayout(header)

        # Scroll Area
        self._container = QWidget()
        self._container.setStyleSheet("background:#f8fafc;")
        self._list = QVBoxLayout(self._container)
        self._list.setContentsMargins(8, 8, 8, 8)
        self._list.setSpacing(6)
        self._list.addStretch()

        self._scroll = QScrollArea()
        self._scroll.setObjectName("TranscriptScrollArea")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea#TranscriptScrollArea{background:#f8fafc; border:1px solid #cbd5e1; border-radius:8px;}"
        )
        self._scroll.setWidget(self._container)
        root_lay.addWidget(self._scroll, 1)

        # Live mode: provisional words are shown INLINE as a lighter tail of the latest card (or of a pending card
        # in the same layout after a long pause) and replaced in place when the final text arrives.
        self._partials: dict[str, tuple[int, str]] = {}
        self._pending_row: "ParagraphRow | None" = None

    def set_expanded_label(self, is_expanded: bool) -> None:
        self.expand_btn.setText("❐ Restore" if is_expanded else "◧ Expand")

    def _on_mode_switched(self, mode_id: int) -> None:
        self._current_mode = "paragraphs" if mode_id == 1 else "raw"
        self._refresh_visibility()

    def _refresh_visibility(self) -> None:
        is_para = self._current_mode == "paragraphs"
        for p in self._paragraphs.values():
            p.setVisible(is_para)
        for r in self._raw_chunks.values():
            r.setVisible(not is_para)

    JOIN_GAP_MS = 1500  # same pause that splits paragraphs on the server (transcript_assembler)

    def _at_bottom(self) -> bool:
        bar = self._scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - 40

    def _last_paragraph(self) -> "ParagraphRow | None":
        rows = list(self._paragraphs.values())
        return max(rows, key=lambda r: r.offset_ms) if rows else None

    def _prune_finalized(self, end_ms: int) -> None:
        for k, (start, _) in list(self._partials.items()):
            if start < end_ms:
                self._partials.pop(k, None)

    def _render_partials(self) -> None:
        follow = self._at_bottom()
        last = self._last_paragraph()
        if not self._partials:
            target = None
        else:
            start = min(st for st, _ in self._partials.values())
            if (last is not None and last.offset_ms <= start and start - last.end_ms < self.JOIN_GAP_MS
                    and not last.text.rstrip().endswith("?")):  # server starts a new card after a question
                target = last
            else:
                if self._pending_row is None:
                    row = ParagraphRow({"id": "__live__", "start_offset_ms": start, "end_offset_ms": start,
                                        "text": ""})
                    row.ask_requested.connect(self.ask_requested)
                    row.use_requested.connect(self.use_requested)
                    row.append_requested.connect(self.append_requested)
                    row.setVisible(self._current_mode == "paragraphs")
                    self._list.insertWidget(self._list.count() - 1, row)
                    self._pending_row = row
                target = self._pending_row
        body = " ".join(t for _, t in sorted(self._partials.values()))
        for r in list(self._paragraphs.values()) + ([self._pending_row] if self._pending_row else []):
            r.set_tail(body if r is target else "")
        if self._pending_row is not None and target is not self._pending_row:
            self._pending_row.deleteLater()
            self._pending_row = None
        if follow:
            QTimer.singleShot(15, self.scroll_to_bottom)

    def set_partial(self, item_id: str, text: str, start_ms: int, final: bool) -> None:
        if final or not text:
            self._partials.pop(item_id, None)
        else:
            self._partials[item_id] = (start_ms, text)
        self._render_partials()

    def clear_partials(self) -> None:
        self._partials.clear()
        self._render_partials()

    def clear(self) -> None:
        self._partials.clear()
        if self._pending_row is not None:
            self._pending_row.deleteLater()
            self._pending_row = None
        for p in self._paragraphs.values():
            p.deleteLater()
        for r in self._raw_chunks.values():
            r.deleteLater()
        self._paragraphs.clear()
        self._raw_chunks.clear()

    def add_utterance(self, utt: dict) -> None:
        """Adds or updates a continuous conversational paragraph in real time."""
        u_id = utt["id"]
        bar = self._scroll.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 40

        self._prune_finalized(int(utt.get("end_offset_ms", utt["start_offset_ms"])))
        pending = self._pending_row
        if u_id in self._paragraphs:
            self._paragraphs[u_id].update_utterance(utt)
        elif pending is not None and pending.offset_ms >= utt["start_offset_ms"] - 50:
            pending.utterance_id = u_id
            pending.offset_ms = utt["start_offset_ms"]
            pending.ts_lbl.setText(fmt_ts(pending.offset_ms))
            pending.update_utterance(utt)
            self._paragraphs[u_id] = pending
            self._pending_row = None
        else:
            row = ParagraphRow(utt)
            row.ask_requested.connect(self.ask_requested)
            row.use_requested.connect(self.use_requested)
            row.append_requested.connect(self.append_requested)
            row.setVisible(self._current_mode == "paragraphs")
            self._paragraphs[u_id] = row
            idx = self._list.indexOf(pending) if pending is not None else self._list.count() - 1
            self._list.insertWidget(idx, row)   # keep a pending live card after finalized history

        self._render_partials()
        if at_bottom:
            QTimer.singleShot(15, self.scroll_to_bottom)

    def scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def add_segment(self, seg: dict) -> None:
        """Stores raw chunk for traceability / raw chunk view."""
        s_id = seg["id"]
        if s_id in self._raw_chunks:
            return
        row = RawChunkRow(seg)
        row.setVisible(self._current_mode == "raw")
        self._raw_chunks[s_id] = row
        self._list.insertWidget(self._list.count() - 1, row)

    def add_interval_marker(self, label: str) -> None:
        """Adds a clear pause/resume boundary marker."""
        marker = QLabel(f"— {label} —")
        marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        marker.setStyleSheet("color:#94a3b8; font-size:11px; font-style:italic; padding:4px 0; border:none;")
        self._list.insertWidget(self._list.count() - 1, marker)

    def mark_question(self, target_id: str, text: str) -> None:
        for p in self._paragraphs.values():
            if p.utterance_id == target_id or text in p.text:
                if text not in p.questions:
                    p.questions.append(text)
                p._apply_question_style()
                p.ask_btn.show()

    def selected_text(self) -> str:
        return " ".join(t for p in self._paragraphs.values() if (t := p.selected_text()))

    def copy_all(self) -> None:
        rows = sorted(self._paragraphs.values(), key=lambda p: p.offset_ms)
        text = "\n\n".join(f"[{fmt_ts(p.offset_ms)}] {p.text}" for p in rows)
        QGuiApplication.clipboard().setText(text)

    def copy_clean_text(self) -> None:
        rows = sorted(self._paragraphs.values(), key=lambda p: p.offset_ms)
        text = "\n\n".join(p.text for p in rows)
        QGuiApplication.clipboard().setText(text)

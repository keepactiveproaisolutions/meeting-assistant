"""👤 Upload CV / Documents — upload, preview, label, replace, remove and select documents per meeting."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ui.privacy import set_window_capture_exclusion

DOC_TYPES = [("cv", "CV / Resume"), ("jd", "Job description"), ("project", "Client project notes"), ("work", "My project / work sample"),
             ("supporting", "Supporting document")]
SUPPORTED = ("Supported: PDF with selectable text, Word .docx, plain text .txt/.md. Scanned/image-only PDFs "
             "are rejected with a clear message (OCR is not available).")
STATUS_STYLE = {"ready": ("✅ Ready", "#047857"), "failed": ("❌ Failed", "#b91c1c"),
                "processing": ("⏳ Indexing…", "#b45309"), "pending": ("⏳ Queued…", "#b45309")}

BTN = ("QPushButton{background:#f1f5f9;color:#0f172a;font-weight:600;padding:5px 10px;border-radius:5px;"
       "border:1px solid #cbd5e1;font-size:12px;} QPushButton:hover{background:#e2e8f0;}"
       "QPushButton:disabled{color:#94a3b8;}")
PRIMARY = ("QPushButton{background:#2563eb;color:#fff;font-weight:700;padding:5px 12px;border-radius:5px;"
           "border:none;font-size:12px;} QPushButton:hover{background:#1d4ed8;}")


class DocumentsDialog(QDialog):
    def __init__(self, api, meeting_id: str | None, parent: QWidget | None = None,
                 privacy_enabled: bool = True) -> None:
        super().__init__(parent)
        self.api, self.meeting_id, self._privacy = api, meeting_id, privacy_enabled
        self.docs: list[dict] = []
        self.setWindowTitle("👤 CV & Documents")
        self.resize(980, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        head = QLabel("<b>Your documents</b> — answers and Demo Prep use only the documents ticked for this meeting. "
                      "Text is treated as evidence, never as instructions.")
        head.setWordWrap(True)
        head.setStyleSheet("font-size:12.5px;color:#0f172a;")
        root.addWidget(head)
        hint = QLabel(SUPPORTED)
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size:11.5px;color:#475569;")
        root.addWidget(hint)

        up = QHBoxLayout()
        up.addWidget(QLabel("Type:"))
        self.type_combo = QComboBox()
        for value, label in DOC_TYPES:
            self.type_combo.addItem(label, value)
        up.addWidget(self.type_combo)
        self.upload_btn = QPushButton("⬆ Upload file…")
        self.upload_btn.setStyleSheet(PRIMARY)
        self.upload_btn.clicked.connect(self._upload)
        up.addWidget(self.upload_btn)
        up.addStretch()
        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.setStyleSheet(BTN)
        self.refresh_btn.clicked.connect(self.reload)
        up.addWidget(self.refresh_btn)
        root.addLayout(up)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Use in meeting", "File", "Type", "Status", "Details"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setStyleSheet("QTableWidget{font-size:12px;} QHeaderView::section{font-weight:700;padding:4px;}")
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        root.addWidget(self.table, 1)

        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setStyleSheet("font-size:12px;color:#334155;")
        root.addWidget(self.msg)

        acts = QHBoxLayout()
        self.preview_btn = QPushButton("👁 Preview text")
        self.replace_btn = QPushButton("♻ Replace file…")
        self.remove_btn = QPushButton("🗑 Remove")
        for b, fn in ((self.preview_btn, self._preview), (self.replace_btn, self._replace), (self.remove_btn, self._remove)):
            b.setStyleSheet(BTN)
            b.clicked.connect(fn)
            acts.addWidget(b)
        acts.addStretch()
        self.save_sel_btn = QPushButton("✔ Save meeting selection")
        self.save_sel_btn.setStyleSheet(PRIMARY)
        self.save_sel_btn.clicked.connect(self._save_selection)
        self.save_sel_btn.setEnabled(bool(meeting_id))
        acts.addWidget(self.save_sel_btn)
        close = QPushButton("Close")
        close.setStyleSheet(BTN)
        close.clicked.connect(self.accept)
        acts.addWidget(close)
        root.addLayout(acts)

        if not meeting_id:
            self.msg.setText("Join or create a meeting to choose which documents it uses.")

        self._poll = QTimer(self)
        self._poll.setInterval(2000)
        self._poll.timeout.connect(self.reload)
        self.reload()
        self._sync_buttons()

    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._privacy:
            set_window_capture_exclusion(int(self.winId()), True)

    def _err(self, title: str, exc: Exception) -> None:
        QMessageBox.warning(self, title, str(exc))

    def reload(self) -> None:
        if self.api is None:
            self.msg.setText("Not connected to the backend.")
            return
        try:
            if self.meeting_id:
                data = self.api.meeting_documents(self.meeting_id)
                docs = data["documents"]
            else:
                docs = [{**d, "selected": d["status"] == "ready"} for d in self.api.list_documents()]
        except Exception as exc:  # noqa: BLE001
            self.msg.setText(f"Could not load documents: {exc}")
            return
        keep = self._selected_id()
        self.docs = docs
        self.table.setRowCount(len(docs))
        for r, d in enumerate(docs):
            cb = QCheckBox()
            cb.setChecked(bool(d.get("selected")))
            cb.setEnabled(d["status"] == "ready" and bool(self.meeting_id))
            cell = QWidget()
            lay = QHBoxLayout(cell)
            lay.setContentsMargins(10, 0, 0, 0)
            lay.addWidget(cb)
            self.table.setCellWidget(r, 0, cell)

            name = QTableWidgetItem(d["filename"])
            name.setData(Qt.ItemDataRole.UserRole, d["id"])
            self.table.setItem(r, 1, name)

            combo = QComboBox()
            for value, label in DOC_TYPES:
                combo.addItem(label, value)
            values = [v for v, _ in DOC_TYPES]
            combo.setCurrentIndex(values.index(d["doc_type"]) if d["doc_type"] in values else values.index("supporting"))
            combo.currentIndexChanged.connect(lambda _i, did=d["id"], c=combo: self._set_type(did, c.currentData()))
            self.table.setCellWidget(r, 2, combo)

            label, color = STATUS_STYLE.get(d["status"], (d["status"], "#334155"))
            st = QTableWidgetItem(label)
            st.setForeground(QColor(color))
            st.setToolTip(d.get("error") or "")
            self.table.setItem(r, 3, st)

            if d["status"] == "failed":
                det = d.get("error") or "Failed"
            elif d["status"] == "ready":
                bits = [f"{d.get('chunk_count', 0)} passages"]
                if d.get("page_count"):
                    bits.append(f"{d['page_count']} page(s)")
                if d.get("char_count"):
                    bits.append(f"{d['char_count']:,} chars")
                if d.get("warnings"):
                    bits.append("⚠ " + "; ".join(d["warnings"]))
                det = " · ".join(bits)
            else:
                det = "Extracting and indexing…"
            det_item = QTableWidgetItem(det)
            det_item.setToolTip(det)
            self.table.setItem(r, 4, det_item)
            if d["id"] == keep:
                self.table.selectRow(r)
        busy = any(d["status"] in ("pending", "processing") for d in docs)
        if busy and not self._poll.isActive():
            self._poll.start()
        elif not busy and self._poll.isActive():
            self._poll.stop()
        if not docs:
            self.msg.setText("No documents yet. Upload your CV and the job description to ground answers.")
        self._sync_buttons()

    def _selected_id(self) -> str | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 1)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selected_doc(self) -> dict | None:
        did = self._selected_id()
        return next((d for d in self.docs if d["id"] == did), None)

    def _sync_buttons(self) -> None:
        has = self._selected_doc() is not None
        for b in (self.preview_btn, self.replace_btn, self.remove_btn):
            b.setEnabled(has)

    # ------------------------------------------------------------------
    def _pick_file(self, title: str) -> str | None:
        path, _ = QFileDialog.getOpenFileName(self, title, "",
                                              "Documents (*.pdf *.docx *.txt *.md);;All files (*)")
        return path or None

    def _upload(self) -> None:
        path = self._pick_file("Upload CV / document")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            res = self.api.upload_document(path, self.type_combo.currentData())
            self.msg.setText(f"“{res['filename']}” is already uploaded (identical file) — type updated."
                             if res.get("duplicate") else f"Uploaded “{res['filename']}” — extracting and indexing…")
        except Exception as exc:  # noqa: BLE001
            self._err("Upload not accepted", exc)
        finally:
            QApplication.restoreOverrideCursor()
        self.reload()

    def _replace(self) -> None:
        d = self._selected_doc()
        if not d:
            return
        path = self._pick_file(f"Replace “{d['filename']}”")
        if not path:
            return
        if QMessageBox.question(self, "Replace document",
                                f"Replace “{d['filename']}” with the selected file?\nIts meeting selections are kept.") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self.api.replace_document(d["id"], path)
            self.msg.setText("Replacement uploaded — re-indexing (unchanged passages reuse their embeddings).")
        except Exception as exc:  # noqa: BLE001
            self._err("Replace failed", exc)
        self.reload()

    def _remove(self) -> None:
        d = self._selected_doc()
        if not d:
            return
        if QMessageBox.question(self, "Remove document",
                                f"Permanently remove “{d['filename']}” and its indexed passages?\n"
                                "Past answers keep their quoted sources.") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.api.delete_document(d["id"])
            self.msg.setText(f"Removed “{d['filename']}”.")
        except Exception as exc:  # noqa: BLE001
            self._err("Remove failed", exc)
        self.reload()

    def _set_type(self, document_id: str, doc_type: str) -> None:
        try:
            self.api.set_document_type(document_id, doc_type)
            self.msg.setText("Document type updated.")
        except Exception as exc:  # noqa: BLE001
            self._err("Could not change type", exc)

    def _save_selection(self) -> None:
        if not self.meeting_id:
            return
        chosen, ready = [], []
        for r, d in enumerate(self.docs):
            if d["status"] != "ready":
                continue
            ready.append(d["id"])
            cb = self.table.cellWidget(r, 0).findChild(QCheckBox)
            if cb and cb.isChecked():
                chosen.append(d["id"])
        ids = None if set(chosen) == set(ready) else chosen  # all ticked = "all my documents" (incl. future uploads)
        try:
            self.api.set_meeting_documents(self.meeting_id, ids)
            self.msg.setText("This meeting now uses all of your ready documents." if ids is None
                             else f"This meeting now uses {len(chosen)} selected document(s).")
        except Exception as exc:  # noqa: BLE001
            self._err("Could not save selection", exc)
        self.reload()

    def _preview(self) -> None:
        d = self._selected_doc()
        if not d:
            return
        try:
            full = self.api.get_document(d["id"])
        except Exception as exc:  # noqa: BLE001
            self._err("Preview failed", exc)
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Extracted text — {full['filename']}")
        dlg.resize(820, 640)
        lay = QVBoxLayout(dlg)
        meta = [f"Status: {full['status']}", f"Type: {full['doc_type_label']}",
                f"Passages: {full.get('chunk_count', 0)}"]
        if full.get("extraction_method"):
            meta.append(f"Method: {full['extraction_method']}")
        if full.get("sections"):
            meta.append("Sections: " + ", ".join(full["sections"][:15]))
        if full.get("error"):
            meta.append(f"Error: {full['error']}")
        if full.get("warnings"):
            meta.append("Warnings: " + "; ".join(full["warnings"]))
        info = QLabel("\n".join(meta))
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.setStyleSheet("font-size:11.5px;color:#334155;")
        lay.addWidget(info)
        text = QPlainTextEdit(full.get("text") or "(no text extracted)")
        text.setReadOnly(True)
        text.setStyleSheet("font-family:Consolas,monospace;font-size:12px;")
        lay.addWidget(text, 1)
        note = QLabel("Contact details (email, phone, postal address) are redacted before indexing."
                      + ("  Showing the first 20,000 characters." if full.get("text_truncated") else ""))
        note.setStyleSheet("font-size:11px;color:#64748b;")
        lay.addWidget(note)
        btn = QPushButton("Close")
        btn.setStyleSheet(BTN)
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignRight)
        if self._privacy:
            dlg.show()
            set_window_capture_exclusion(int(dlg.winId()), True)
        dlg.exec()

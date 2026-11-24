"""Safe, structured Markdown + responsive SVG diagram AnswerView for the PySide6 desktop app."""
from __future__ import annotations

import re

from PySide6.QtCore import QByteArray, QEvent, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QMouseEvent, QPainter, QPixmap, QTextDocument
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ui.diagram_renderer import DiagramResult, StructuredAnswer, parse_structured_answer

_RAW_HTML_TAG_RE = re.compile(r"</?[A-Za-z!][^>]*>")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_UNSAFE_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*(?:javascript|data|vbscript|file):[^)]*\)", re.I)
_DIAGRAM_PLACEHOLDER_RE = re.compile(r"\[\[DIAGRAM_(\d+)\]\]")

_DOC_CSS = """
body {
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 13.5px;
    line-height: 1.58;
    color: #0f172a;
}
p {
    margin-top: 0px;
    margin-bottom: 7px;
}
h1, h2, h3, h4 {
    color: #1e3a8a;
    font-weight: 700;
    margin-top: 9px;
    margin-bottom: 5px;
}
h1 { font-size: 16.5px; }
h2 { font-size: 15px; }
h3 { font-size: 14px; }
h4 { font-size: 13.5px; }
ul, ol {
    margin-top: 3px;
    margin-bottom: 7px;
    padding-left: 18px;
}
li {
    margin-bottom: 3px;
}
code {
    font-family: 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    background-color: #e2e8f0;
    color: #0f172a;
}
pre {
    font-family: 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    background-color: #0f172a;
    color: #f8fafc;
    padding: 8px 10px;
    margin-top: 5px;
    margin-bottom: 7px;
    white-space: pre-wrap;
}
table {
    border-collapse: collapse;
    margin-top: 6px;
    margin-bottom: 8px;
    width: 100%;
}
th {
    background-color: #e2e8f0;
    color: #0f172a;
    font-weight: 700;
    padding: 5px 7px;
    border: 1px solid #cbd5e1;
}
td {
    background-color: #ffffff;
    color: #1e293b;
    padding: 5px 7px;
    border: 1px solid #cbd5e1;
}
"""


def sanitize_markdown_for_qt(md: str) -> str:
    """Escape raw HTML tags outside fenced/inline code so Qt's Markdown parser never executes untrusted HTML."""
    if not md:
        return ""
    lines = md.split("\n")
    out: list[str] = []
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        parts = re.split(r"(`[^`]+`)", line)
        safe_parts: list[str] = []
        for idx, part in enumerate(parts):
            if idx % 2 == 1:
                safe_parts.append(part)
            else:
                s = _RAW_HTML_TAG_RE.sub(lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"), part)
                s = _MD_IMAGE_RE.sub(r"[remote image blocked: \1]", s)
                s = _UNSAFE_MD_LINK_RE.sub(r"\1 (unsafe link blocked)", s)
                safe_parts.append(s)
        out.append("".join(safe_parts))
    return "\n".join(out)


def render_svg_to_pixmap(svg_text: str, target_width: int = 760, scale: float = 1.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    if not renderer.isValid():
        pm = QPixmap(320, 70)
        pm.fill(QColor("#fef2f2"))
        return pm
    default_size = renderer.defaultSize()
    w = max(200, default_size.width())
    h = max(100, default_size.height())
    if scale == 1.0 and target_width > 0:
        ratio = target_width / float(w)
        w = max(180, int(w * ratio))
        h = max(80, int(h * ratio))
    else:
        w = max(180, int(w * scale))
        h = max(80, int(h * scale))

    dpr = 2.0
    img = QImage(int(w * dpr), int(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#ffffff"))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, w * dpr, h * dpr))
    painter.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


class DiagramZoomDialog(QDialog):
    """Interactive zoomable dialog for inspecting architecture and ER diagrams."""

    def __init__(self, diagram: DiagramResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.diagram = diagram
        self._scale = 1.0
        self.setWindowTitle(f"Diagram Viewer — {'ER Diagram' if diagram.kind == 'erDiagram' else 'Architecture Flow'}")
        self.resize(1040, 720)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)
        toolbar = QHBoxLayout()

        btn_style = (
            "QPushButton{background:#f1f5f9;color:#0f172a;border:1px solid #cbd5e1;"
            "border-radius:5px;padding:5px 12px;font-weight:600;font-size:12px;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )

        zoom_out_btn = QPushButton("− Zoom Out")
        zoom_out_btn.setStyleSheet(btn_style)
        zoom_out_btn.clicked.connect(lambda: self._set_scale(self._scale - 0.25))

        zoom_reset_btn = QPushButton("Readable")
        zoom_reset_btn.setStyleSheet(btn_style)
        zoom_reset_btn.clicked.connect(lambda: self._set_scale(1.0))
        fit_btn = QPushButton("Fit all")
        fit_btn.setStyleSheet(btn_style)
        fit_btn.clicked.connect(self._fit)

        zoom_in_btn = QPushButton("+ Zoom In")
        zoom_in_btn.setStyleSheet(btn_style)
        zoom_in_btn.clicked.connect(lambda: self._set_scale(self._scale + 0.25))

        self.zoom_lbl = QLabel("100%")
        self.zoom_lbl.setStyleSheet("font-weight:700;color:#1e3a8a;padding:0 8px;font-size:13px;")

        copy_src_btn = QPushButton("📋 Copy Mermaid")
        copy_src_btn.setStyleSheet(btn_style)
        copy_src_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.diagram.source))

        copy_svg_btn = QPushButton("📋 Copy SVG")
        copy_svg_btn.setStyleSheet(btn_style)
        copy_svg_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.diagram.svg))

        save_btn = QPushButton("💾 Save SVG…")
        save_btn.setStyleSheet(btn_style)
        save_btn.clicked.connect(self._save_svg)

        close_btn = QPushButton("✕ Close")
        close_btn.setStyleSheet(
            "QPushButton{background:#1e40af;color:#ffffff;border:none;border-radius:5px;padding:5px 14px;font-weight:700;font-size:12px;}"
        )
        close_btn.clicked.connect(self.accept)

        toolbar.addWidget(zoom_out_btn)
        toolbar.addWidget(zoom_reset_btn)
        toolbar.addWidget(fit_btn)
        toolbar.addWidget(zoom_in_btn)
        toolbar.addWidget(self.zoom_lbl)
        toolbar.addStretch()
        toolbar.addWidget(copy_src_btn)
        toolbar.addWidget(copy_svg_btn)
        toolbar.addWidget(save_btn)
        toolbar.addWidget(close_btn)
        root.addLayout(toolbar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setStyleSheet("QScrollArea{background:#f8fafc;border:1px solid #cbd5e1;border-radius:8px;}")
        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_label.setStyleSheet("background:#ffffff;padding:12px;")
        self.scroll.setWidget(self.img_label)
        root.addWidget(self.scroll, 1)
        hint = QLabel("Drag to move · mouse wheel or +/− to zoom · Fit shows the whole diagram")
        hint.setStyleSheet("color:#64748b;font-size:11px;")
        root.addWidget(hint)
        self._drag = None
        self.scroll.viewport().installEventFilter(self)
        self.scroll.viewport().setCursor(Qt.CursorShape.OpenHandCursor)

        self._render_scaled()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.scroll.viewport():
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag = event.position().toPoint()
                self.scroll.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                return True
            if et == QEvent.Type.MouseMove and self._drag is not None:
                p = event.position().toPoint()
                d = p - self._drag
                self._drag = p
                self.scroll.horizontalScrollBar().setValue(self.scroll.horizontalScrollBar().value() - d.x())
                self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().value() - d.y())
                return True
            if et == QEvent.Type.MouseButtonRelease:
                self._drag = None
                self.scroll.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
                return True
            if et == QEvent.Type.Wheel:
                self._set_scale(self._scale * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15))
                return True
        return super().eventFilter(obj, event)

    def _fit(self) -> None:
        r = QSvgRenderer(QByteArray(self.diagram.svg.encode("utf-8")))
        nat = r.defaultSize() if r.isValid() else QSize(1000, 400)
        avail = max(300, self.scroll.viewport().width() - 40)
        self._set_scale(avail / float(max(1, nat.width())))

    def _set_scale(self, new_scale: float) -> None:
        self._scale = max(0.2, min(6.0, round(new_scale, 3)))
        self.zoom_lbl.setText(f"{int(self._scale * 100)}%")
        self._render_scaled()

    def _render_scaled(self) -> None:
        pm = render_svg_to_pixmap(self.diagram.svg, target_width=0, scale=self._scale)
        self.img_label.setPixmap(pm)
        self.img_label.resize(pm.deviceIndependentSize().toSize())

    def _save_svg(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Diagram as SVG", f"diagram-{self.diagram.index + 1}.svg", "SVG Files (*.svg)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.diagram.svg)


class ResponsiveSvgCanvas(QWidget):
    """Paints an SVG scaled cleanly to fit the available panel width while preserving aspect ratio."""

    clicked = Signal()

    def __init__(self, svg_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")), self)
        default_size = self._renderer.defaultSize() if self._renderer.isValid() else QSize(640, 220)
        self._nat_w = max(200, default_size.width())
        self._nat_h = max(90, default_size.height())
        self._aspect = self._nat_h / float(self._nat_w)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to expand and zoom diagram")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._update_fitted_height(460)

    def _update_fitted_height(self, avail_w: int) -> None:
        w = max(200, avail_w - 12)
        target_h = int(w * self._aspect) + 12
        clamped_h = max(110, min(380, target_h))
        if self.minimumHeight() != clamped_h or self.maximumHeight() != clamped_h:
            self.setFixedHeight(clamped_h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_fitted_height(event.size().width())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.fillRect(rect, QColor("#ffffff"))
        if not self._renderer.isValid():
            return
        # Fit inside rect preserving aspect ratio
        avail_w = float(rect.width() - 8)
        avail_h = float(rect.height() - 8)
        scale = min(avail_w / self._nat_w, avail_h / self._nat_h)
        draw_w = self._nat_w * scale
        draw_h = self._nat_h * scale
        x = rect.left() + (rect.width() - draw_w) / 2.0
        y = rect.top() + (rect.height() - draw_h) / 2.0
        self._renderer.render(painter, QRectF(x, y, draw_w, draw_h))
        painter.end()


class DiagramBlockWidget(QFrame):
    """Displays a responsive fit-to-width SVG diagram inside an AnswerCard with one-click Expand/Zoom."""

    def __init__(self, diagram: DiagramResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.diagram = diagram
        self.setObjectName("DiagramBlock")
        self.setStyleSheet(
            "QFrame#DiagramBlock{background:#ffffff;border:1px solid #cbd5e1;border-radius:7px;}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(4)
        badge = QLabel(
            f"📊 Architecture #{diagram.index + 1}"
            if diagram.kind != "erDiagram"
            else f"🗄️ ER Schema #{diagram.index + 1}"
        )
        badge.setStyleSheet("font-size:11px;font-weight:700;color:#1e3a8a;background:transparent;border:none;")
        header.addWidget(badge)
        header.addStretch()

        if diagram.ok and diagram.svg:
            zoom_btn = QPushButton("🔍 Expand")
            zoom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            zoom_btn.setToolTip("Expand and zoom diagram")
            zoom_btn.setStyleSheet(
                "QPushButton{background:#dbeafe;color:#1e40af;font-weight:700;font-size:10.5px;padding:2px 7px;border-radius:4px;border:1px solid #93c5fd;}"
                "QPushButton:hover{background:#bfdbfe;}"
            )
            zoom_btn.clicked.connect(self._open_zoom)
            header.addWidget(zoom_btn)

            save_btn = QPushButton("💾 SVG")
            save_btn.setToolTip("Save diagram as SVG")
            save_btn.setStyleSheet(
                "QPushButton{background:#f1f5f9;color:#334155;font-size:10.5px;padding:2px 6px;border-radius:4px;border:1px solid #cbd5e1;}"
            )
            save_btn.clicked.connect(self._save_svg)
            header.addWidget(save_btn)

        copy_src_btn = QPushButton("📋 Source")
        copy_src_btn.setToolTip("Copy Mermaid diagram source")
        copy_src_btn.setStyleSheet(
            "QPushButton{background:#f1f5f9;color:#334155;font-size:10.5px;padding:2px 6px;border-radius:4px;border:1px solid #cbd5e1;}"
        )
        copy_src_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.diagram.source))
        header.addWidget(copy_src_btn)

        lay.addLayout(header)

        if diagram.ok and diagram.svg:
            self.canvas = ResponsiveSvgCanvas(diagram.svg, self)
            self.canvas.clicked.connect(self._open_zoom)
            lay.addWidget(self.canvas)
        else:
            err_lbl = QLabel(f"⚠️ Diagram fallback ({diagram.error or 'Unrecognized diagram syntax'}):")
            err_lbl.setTextFormat(Qt.TextFormat.PlainText)
            err_lbl.setStyleSheet("color:#b45309;font-size:11.5px;font-weight:700;border:none;")
            lay.addWidget(err_lbl)
            src_box = QTextBrowser()
            src_box.setPlainText(diagram.source)
            src_box.setMaximumHeight(110)
            src_box.setStyleSheet(
                "font-family:Consolas, monospace;font-size:11px;background:#fffbeb;color:#78350f;border:1px solid #fcd34d;"
            )
            lay.addWidget(src_box)

    def _open_zoom(self) -> None:
        dlg = DiagramZoomDialog(self.diagram, self)
        dlg.exec()

    def _save_svg(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Diagram as SVG", f"diagram-{self.diagram.index + 1}.svg", "SVG Files (*.svg)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.diagram.svg)


_CODE_TOKEN_RE = re.compile(
    r"(?P<comment>#[^\n]*|--[^\n]*|/\*[\s\S]*?\*/)"
    r"|(?P<string>\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|f?\"(?:\\.|[^\"\\\n])*\"|f?'(?:\\.|[^'\\\n])*')"
    r"|(?P<decorator>@[A-Za-z_][A-Za-z0-9_.]*)"
    r"|(?P<number>\b\d+(?:_\d+)*(?:\.\d+)?\b)"
    r"|(?P<ident>\b[A-Za-z_][A-Za-z0-9_]*\b)"
    r"|(?P<op>[=+\-*/%<>!&|^~:;,.()\[\]{}]+)"
)

_KW_SET = {
    "def", "class", "import", "from", "as", "return", "yield", "if", "elif", "else",
    "for", "while", "in", "not", "and", "or", "is", "try", "except", "finally",
    "raise", "with", "async", "await", "lambda", "pass", "break", "continue",
    "global", "nonlocal", "assert", "del",
    "select", "where", "join", "left", "right", "inner", "outer", "full", "cross",
    "on", "group", "by", "order", "having", "limit", "offset", "insert", "into",
    "values", "update", "set", "delete", "merge", "using", "when", "matched",
    "then", "create", "alter", "drop", "table", "view", "index", "schema",
    "database", "primary", "foreign", "key", "references", "unique", "check",
    "default", "identity", "distkey", "sortkey", "diststyle", "compound",
    "interleaved", "all", "even", "auto", "encode", "copy", "unload", "iam_role",
    "format", "parquet", "csv", "json", "manifest", "statupdate", "compupdate",
    "region", "begin", "transaction", "commit", "rollback", "truncate", "analyze",
    "vacuum", "grant", "revoke", "distinct", "union", "case", "end", "over",
    "partition", "rows", "range", "between", "preceding", "following", "current", "row",
    "asc", "desc", "nulls", "first", "last",
}

_CONST_SET = {"true", "false", "none", "null", "off", "self", "cls"}

_TYPE_BUILTIN_SET = {
    "int", "str", "float", "bool", "list", "dict", "set", "tuple", "bytes", "object",
    "exception", "filenotfounderror", "valueerror", "typeerror", "keyerror", "runtimeerror",
    "zerodivisionerror", "indexerror", "attributeerror", "oserror", "ioerror",
    "print", "open", "len", "range", "enumerate", "zip", "map", "filter", "sorted",
    "sum", "min", "max", "abs", "round", "isinstance", "getattr", "setattr", "hasattr",
    "super", "property", "staticmethod", "classmethod", "dataclass",
    "bigint", "integer", "smallint", "decimal", "numeric", "real", "double", "precision",
    "varchar", "char", "text", "timestamp", "timestamptz", "date", "time", "boolean",
    "varbyte", "sparksession", "dataframe", "window", "deltatable",
    "col", "lit", "coalesce", "row_number", "rank", "dense_rank", "broadcast", "udf",
    "pd", "np", "spark", "df",
}


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def highlight_code_to_html(code: str) -> str:
    """Convert raw source code into a safe, colorful VS-Code Dark+ / CodeSnap HTML <pre> block."""
    if not code:
        return ""
    parts: list[str] = []
    last_idx = 0
    for m in _CODE_TOKEN_RE.finditer(code):
        if m.start() > last_idx:
            parts.append(_html_escape(code[last_idx : m.start()]))
        tok = m.group(0)
        esc = _html_escape(tok)
        kind = m.lastgroup
        if kind == "comment":
            parts.append(f'<span style="color:#6a9955;font-style:italic;">{esc}</span>')
        elif kind == "string":
            parts.append(f'<span style="color:#86efac;">{esc}</span>')
        elif kind == "decorator":
            parts.append(f'<span style="color:#ffa657;font-weight:600;">{esc}</span>')
        elif kind == "number":
            parts.append(f'<span style="color:#f2cc60;">{esc}</span>')
        elif kind == "ident":
            low = tok.lower()
            after = code[m.end() : m.end() + 8]
            if low in _KW_SET:
                parts.append(f'<span style="color:#ff7b72;font-weight:600;">{esc}</span>')
            elif low in _CONST_SET:
                parts.append(f'<span style="color:#79c0ff;font-weight:600;">{esc}</span>')
            elif low in _TYPE_BUILTIN_SET:
                parts.append(f'<span style="color:#d2a8ff;">{esc}</span>')
            elif re.match(r"^\s*\(", after):
                parts.append(f'<span style="color:#79c0ff;">{esc}</span>')
            elif tok[0].isupper():
                parts.append(f'<span style="color:#ffa657;font-weight:600;">{esc}</span>')
            else:
                parts.append(f'<span style="color:#e6edf3;">{esc}</span>')
        elif kind == "op":
            parts.append(f'<span style="color:#94a3b8;">{esc}</span>')
        else:
            parts.append(esc)
        last_idx = m.end()
    if last_idx < len(code):
        parts.append(_html_escape(code[last_idx:]))

    body = "".join(parts)
    return (
        "<pre style=\"margin:0;padding:4px 6px;background-color:#0d1117;color:#e6edf3;"
        "font-family:'Cascadia Mono','Consolas','Courier New',monospace;font-size:12.5px;"
        'line-height:1.52;white-space:pre-wrap;">'
        f"{body}</pre>"
    )


class CodeSnapWidget(QFrame):
    """ChatGPT / CodeSnap-style dark syntax-highlighted code card with macOS dots, language badge, and Copy code button."""

    def __init__(self, lang: str, code: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.lang = (lang or "code").strip().upper()
        self.code = code or ""
        self.setObjectName("CodeSnapCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            "QFrame#CodeSnapCard{background:#0d1117;border:1px solid #30363d;border-radius:8px;}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar (#161b22)
        hdr = QFrame()
        hdr.setObjectName("CodeSnapHeader")
        hdr.setFixedHeight(32)
        hdr.setStyleSheet(
            "QFrame#CodeSnapHeader{background:#161b22;border-bottom:1px solid #21262d;"
            "border-top-left-radius:7px;border-top-right-radius:7px;}"
        )
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(10, 4, 10, 4)
        hdr_lay.setSpacing(8)

        dots_lbl = QLabel(
            '<span style="color:#ff5f56;font-size:13px;">●</span> '
            '<span style="color:#ffbd2e;font-size:13px;">●</span> '
            '<span style="color:#27c93f;font-size:13px;">●</span>'
        )
        dots_lbl.setFixedHeight(20)
        dots_lbl.setStyleSheet("background:transparent;border:none;")
        hdr_lay.addWidget(dots_lbl)

        lang_lbl = QLabel(self.lang)
        lang_lbl.setFixedHeight(20)
        lang_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lang_lbl.setStyleSheet(
            "font-family:'Cascadia Mono',Consolas,monospace;font-size:10.5px;font-weight:700;"
            "color:#79c0ff;background:#1f2937;border:1px solid #3b82f6;border-radius:4px;padding:0px 7px;"
        )
        hdr_lay.addWidget(lang_lbl)
        hdr_lay.addStretch()

        self.copy_btn = QPushButton("📋 Copy code")
        self.copy_btn.setFixedHeight(22)
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setStyleSheet(
            "QPushButton{background:#21262d;color:#e6edf3;border:1px solid #30363d;"
            "border-radius:5px;padding:2px 9px;font-size:11px;font-weight:600;}"
            "QPushButton:hover{background:#30363d;}"
        )
        self.copy_btn.clicked.connect(self._copy)
        hdr_lay.addWidget(self.copy_btn)
        root.addWidget(hdr)

        # Highlighted code browser (#0d1117)
        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(False)
        self.browser.setOpenLinks(False)
        self.browser.setFrameShape(QFrame.Shape.NoFrame)
        self.browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        self.browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.browser.setStyleSheet(
            "QTextBrowser{background:#0d1117;color:#e6edf3;border:none;padding:6px 10px;"
            "border-bottom-left-radius:7px;border-bottom-right-radius:7px;}"
        )
        self.browser.document().setDocumentMargin(4)
        self.browser.setHtml(highlight_code_to_html(self.code))
        root.addWidget(self.browser)
        self._fit_height()

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.code)
        self.copy_btn.setText("✓ Copied")
        QTimer.singleShot(1500, lambda: self.copy_btn.setText("📋 Copy code"))

    def has_selection(self) -> bool:
        return self.browser.textCursor().hasSelection()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_height()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self) -> None:
        vw = self.browser.viewport().width()
        if vw <= 40:
            parent_w = self.parentWidget().width() if self.parentWidget() else 460
            vw = max(200, parent_w - 28)
        self.browser.document().setTextWidth(float(vw))
        doc_h = max(36, int(self.browser.document().size().height()) + 16)
        self.browser.setFixedHeight(doc_h)
        self.setFixedHeight(32 + doc_h + 2)


class AutoHeightMarkdownBrowser(QTextBrowser):
    """Read-only QTextBrowser that renders GitHub-flavored Markdown safely, wraps prose cleanly, and auto-fits height."""

    def __init__(self, bg_color: str = "#ffffff", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bg_color = bg_color
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(140)
        self.setStyleSheet(
            f"QTextBrowser{{background:{bg_color};border:none;color:#0f172a;padding:0px;}}"
        )
        self.document().setDocumentMargin(2)

    def set_markdown_safe(self, md_text: str, font_px: int = 14) -> None:
        cleaned = sanitize_markdown_for_qt(md_text)
        cleaned = _DIAGRAM_PLACEHOLDER_RE.sub(
            lambda m: "", cleaned
        ).strip()
        custom_css = _DOC_CSS.replace("font-size: 13.5px;", f"font-size: {font_px}px;")
        self.document().setDefaultStyleSheet(custom_css)
        self.document().setMarkdown(cleaned, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
        self._fit_height()

    def has_selection(self) -> bool:
        return self.textCursor().hasSelection()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_height()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self) -> None:
        vw = self.viewport().width()
        if vw <= 40:
            parent_w = self.parentWidget().width() if self.parentWidget() else 420
            vw = max(180, parent_w - 24)
        self.document().setTextWidth(float(vw))
        doc_h = int(self.document().size().height()) + 8
        self.setFixedHeight(max(24, doc_h))


class AnswerCardWidget(QFrame):
    """Teleprompter Q&A card with [Copy answer] [A-] [A+] and 4 structured collapsible drawers."""

    font_delta_requested = Signal(int)

    def __init__(self, answer_id: str, spoken_font_px: int = 15, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.answer_id = answer_id
        self.question_text = ""
        self.raw_text = ""
        self.status = "streaming"
        self.sources: list[dict] = []
        self._parsed: StructuredAnswer | None = None
        self._spoken_font_px: int = spoken_font_px

        # Drawer open states: all 4 start collapsed (▸) so Suggested Answer + all 4 drawers fit in view immediately
        self._arch_open: bool = False
        self._db_open: bool = False
        self._impl_open: bool = False
        self._code_open: bool = False

        self.setObjectName("AnswerCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setStyleSheet(
            "QFrame#AnswerCard{background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        # 1. Question Header
        q_top_row = QHBoxLayout()
        q_top_row.setSpacing(6)
        q_micro = QLabel("QUESTION")
        q_micro.setStyleSheet(
            "font-size:10.5px;font-weight:700;color:#64748b;letter-spacing:0.4px;background:transparent;border:none;"
        )
        q_top_row.addWidget(q_micro)
        q_top_row.addStretch()

        self.status_badge = QLabel("Streaming…")
        self.status_badge.setStyleSheet(
            "background:#dbeafe;color:#1d4ed8;font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:8px;"
        )
        q_top_row.addWidget(self.status_badge)
        root.addLayout(q_top_row)

        self.q_label = QLabel("…")
        self.q_label.setWordWrap(True)
        self.q_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.q_label.setStyleSheet("font-size:14px;font-weight:700;color:#0f172a;background:transparent;border:none;")
        root.addWidget(self.q_label)

        # 2. Suggested Spoken Answer Box (Teleprompter View)
        spoken_frame = QFrame()
        spoken_frame.setObjectName("SpokenBox")
        spoken_frame.setStyleSheet(
            "QFrame#SpokenBox{background:#f8fafc;border:none;border-left:3px solid #2563eb;border-radius:4px;}"
        )
        spoken_lay = QVBoxLayout(spoken_frame)
        spoken_lay.setContentsMargins(10, 6, 8, 6)
        spoken_lay.setSpacing(2)

        spoken_hdr = QLabel("Suggested answer")
        spoken_hdr.setStyleSheet(
            "font-size:11px;font-weight:700;color:#1d4ed8;letter-spacing:0.3px;background:transparent;border:none;"
        )
        spoken_lay.addWidget(spoken_hdr)

        self.spoken_browser = AutoHeightMarkdownBrowser(bg_color="#f8fafc", parent=spoken_frame)
        spoken_lay.addWidget(self.spoken_browser)
        root.addWidget(spoken_frame)

        # 3. Teleprompter Action Bar: [Copy answer] [Copy full]   [A-] [A+]
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.copy_spoken_btn = QPushButton("📋 Copy answer")
        self.copy_spoken_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_spoken_btn.setStyleSheet(
            "QPushButton{background:#eff6ff;color:#1d4ed8;font-weight:700;font-size:11.5px;padding:4px 10px;border-radius:4px;border:1px solid #bfdbfe;}"
            "QPushButton:hover{background:#dbeafe;}"
        )
        self.copy_spoken_btn.clicked.connect(self._copy_spoken)

        self.copy_full_btn = QPushButton("📄 Copy full")
        self.copy_full_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_full_btn.setStyleSheet(
            "QPushButton{background:#f8fafc;color:#334155;font-weight:600;font-size:11px;padding:4px 9px;border-radius:4px;border:1px solid #cbd5e1;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )
        self.copy_full_btn.clicked.connect(self._copy_full)

        self.font_dec_btn = QPushButton("A−")
        self.font_dec_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.font_dec_btn.setToolTip("Decrease reading font size")
        self.font_dec_btn.setStyleSheet(
            "QPushButton{background:#f1f5f9;color:#0f172a;font-weight:700;font-size:11px;padding:3px 8px;border-radius:4px;border:1px solid #cbd5e1;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )
        self.font_dec_btn.clicked.connect(lambda: self.font_delta_requested.emit(-1))

        self.font_inc_btn = QPushButton("A+")
        self.font_inc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.font_inc_btn.setToolTip("Increase reading font size")
        self.font_inc_btn.setStyleSheet(
            "QPushButton{background:#f1f5f9;color:#0f172a;font-weight:700;font-size:11px;padding:3px 8px;border-radius:4px;border:1px solid #cbd5e1;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )
        self.font_inc_btn.clicked.connect(lambda: self.font_delta_requested.emit(1))

        btn_row.addWidget(self.copy_spoken_btn)
        btn_row.addWidget(self.copy_full_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.font_dec_btn)
        btn_row.addWidget(self.font_inc_btn)
        root.addLayout(btn_row)

        drawer_btn_style = (
            "QPushButton{text-align:left;background:#f1f5f9;color:#0f172a;font-weight:700;font-size:12px;"
            "padding:5px 10px;border-radius:5px;border:1px solid #e2e8f0;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )

        # Drawer 1: ▸ Architecture diagram
        self.arch_toggle_btn = QPushButton("▾ Architecture diagram")
        self.arch_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.arch_toggle_btn.setStyleSheet(drawer_btn_style)
        self.arch_toggle_btn.clicked.connect(self._toggle_arch)
        self.arch_toggle_btn.setVisible(False)
        root.addWidget(self.arch_toggle_btn)

        self.arch_container = QWidget()
        self.arch_lay = QVBoxLayout(self.arch_container)
        self.arch_lay.setContentsMargins(2, 4, 2, 4)
        self.arch_lay.setSpacing(6)
        self.arch_diagrams_holder = QWidget()
        self.arch_diagrams_lay = QVBoxLayout(self.arch_diagrams_holder)
        self.arch_diagrams_lay.setContentsMargins(0, 0, 0, 0)
        self.arch_diagrams_lay.setSpacing(6)
        self.arch_lay.addWidget(self.arch_diagrams_holder)

        self.draw_frame = QFrame()
        self.draw_frame.setObjectName("DrawBox")
        self.draw_frame.setStyleSheet(
            "QFrame#DrawBox{background:#fffbeb;border:none;border-left:3px solid #f59e0b;border-radius:4px;}"
        )
        draw_lay = QVBoxLayout(self.draw_frame)
        draw_lay.setContentsMargins(10, 6, 8, 6)
        draw_lay.setSpacing(2)
        draw_hdr = QLabel("✏️ HOW TO EXPLAIN / DRAW ON WHITEBOARD")
        draw_hdr.setStyleSheet("font-size:10.5px;font-weight:700;color:#b45309;background:transparent;border:none;")
        draw_lay.addWidget(draw_hdr)
        self.draw_browser = AutoHeightMarkdownBrowser(bg_color="#fffbeb", parent=self.draw_frame)
        draw_lay.addWidget(self.draw_browser)
        self.draw_frame.setVisible(False)
        self.arch_lay.addWidget(self.draw_frame)
        self.arch_container.setVisible(False)
        root.addWidget(self.arch_container)

        # Drawer 2: ▸ Database design
        self.db_toggle_btn = QPushButton("▸ Database design")
        self.db_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.db_toggle_btn.setStyleSheet(drawer_btn_style)
        self.db_toggle_btn.clicked.connect(self._toggle_db)
        self.db_toggle_btn.setVisible(False)
        root.addWidget(self.db_toggle_btn)

        self.db_container = QWidget()
        self.db_lay = QVBoxLayout(self.db_container)
        self.db_lay.setContentsMargins(2, 4, 2, 4)
        self.db_lay.setSpacing(6)
        self.db_diagrams_holder = QWidget()
        self.db_diagrams_lay = QVBoxLayout(self.db_diagrams_holder)
        self.db_diagrams_lay.setContentsMargins(0, 0, 0, 0)
        self.db_diagrams_lay.setSpacing(6)
        self.db_lay.addWidget(self.db_diagrams_holder)
        self.db_sql_holder = QWidget()
        self.db_sql_lay = QVBoxLayout(self.db_sql_holder)
        self.db_sql_lay.setContentsMargins(0, 0, 0, 0)
        self.db_sql_lay.setSpacing(6)
        self.db_lay.addWidget(self.db_sql_holder)
        self.db_container.setVisible(False)
        root.addWidget(self.db_container)

        # Drawer 3: ▸ Implementation details
        self.impl_toggle_btn = QPushButton("▸ Implementation details")
        self.impl_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.impl_toggle_btn.setStyleSheet(drawer_btn_style)
        self.impl_toggle_btn.clicked.connect(self._toggle_impl)
        self.impl_toggle_btn.setVisible(False)
        root.addWidget(self.impl_toggle_btn)

        self.impl_container = QWidget()
        self.impl_lay = QVBoxLayout(self.impl_container)
        self.impl_lay.setContentsMargins(2, 4, 2, 4)
        self.impl_lay.setSpacing(4)
        self.tech_browser = AutoHeightMarkdownBrowser(bg_color="#ffffff", parent=self.impl_container)
        self.impl_lay.addWidget(self.tech_browser)
        self.impl_container.setVisible(False)
        root.addWidget(self.impl_container)

        # Drawer 4: ▸ Example code
        self.code_toggle_btn = QPushButton("▸ Example code")
        self.code_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.code_toggle_btn.setStyleSheet(drawer_btn_style)
        self.code_toggle_btn.clicked.connect(self._toggle_code)
        self.code_toggle_btn.setVisible(False)
        root.addWidget(self.code_toggle_btn)

        self.code_container = QWidget()
        self.code_lay = QVBoxLayout(self.code_container)
        self.code_lay.setContentsMargins(2, 4, 2, 4)
        self.code_lay.setSpacing(6)
        self.code_blocks_holder = QWidget()
        self.code_blocks_lay = QVBoxLayout(self.code_blocks_holder)
        self.code_blocks_lay.setContentsMargins(0, 0, 0, 0)
        self.code_blocks_lay.setSpacing(6)
        self.code_lay.addWidget(self.code_blocks_holder)
        self.code_container.setVisible(False)
        root.addWidget(self.code_container)

        # 5. Document sources
        self.sources_lbl = QLabel()
        self.sources_lbl.setWordWrap(True)
        self.sources_lbl.setStyleSheet("font-size:11px;color:#475569;background:transparent;border:none;")
        self.sources_lbl.setVisible(False)
        root.addWidget(self.sources_lbl)

        self._rendered_arch_sig: tuple = ()
        self._rendered_db_sig: tuple = ()
        self._rendered_sql_sig: tuple = ()
        self._rendered_code_sig: tuple = ()

    def set_spoken_font_px(self, px: int) -> None:
        self._spoken_font_px = px
        if self._parsed:
            self.update_card(self.question_text, self.raw_text, self.status, self.sources)

    def has_user_selection(self) -> bool:
        return (
            self.spoken_browser.has_selection()
            or self.tech_browser.has_selection()
            or self.draw_browser.has_selection()
        )

    def update_card(self, question: str, text: str, status: str, sources: list[dict], timing: str = "") -> None:
        self.question_text = question
        self.raw_text = text
        self.status = status
        self.sources = sources

        self.q_label.setText(question)
        if status == "streaming":
            self.status_badge.setText("Streaming…")
            self.status_badge.setStyleSheet(
                "background:#dbeafe;color:#1d4ed8;font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:8px;"
            )
        elif status == "completed":
            self.status_badge.setText("Ready" + timing)
            self.status_badge.setStyleSheet(
                "background:#dcfce7;color:#15803d;font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:8px;"
            )
        else:
            self.status_badge.setText("Failed")
            self.status_badge.setStyleSheet(
                "background:#fee2e2;color:#b91c1c;font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:8px;"
            )

        is_complete = status == "completed"
        parsed = parse_structured_answer(text or "", is_complete=is_complete)
        self._parsed = parsed

        spoken_md = parsed.spoken_markdown.strip()
        if not spoken_md:
            spoken_md = "*Generating answer…*" if status == "streaming" else "*(No answer text)*"
        elif status == "streaming" and not parsed.technical_markdown:
            spoken_md += " ▌"
        if status == "failed":
            spoken_md += "\n\n**Error:** Could not generate an answer."

        self.spoken_browser.set_markdown_safe(spoken_md, font_px=self._spoken_font_px)

        arch_diagrams = [d for d in parsed.diagrams if d.kind != "erDiagram"]
        er_diagrams = [d for d in parsed.diagrams if d.kind == "erDiagram"]
        sql_blocks = [b for b in parsed.code_blocks if (b.get("language") or "").lower() == "sql"]
        example_blocks = [b for b in parsed.code_blocks if (b.get("language") or "").lower() != "sql"]

        impl_md = re.sub(r"\[\[DIAGRAM_\d+\]\]", "", parsed.technical_markdown or "")
        impl_md = re.sub(r"```[\s\S]*?```", "", impl_md).strip()

        # 1. Architecture diagram drawer
        has_arch = bool(arch_diagrams or parsed.how_to_draw_markdown.strip())
        self.arch_toggle_btn.setVisible(has_arch)
        if has_arch:
            arrow = "▾" if self._arch_open else "▸"
            self.arch_toggle_btn.setText(f"{arrow} Architecture diagram")
            self.arch_container.setVisible(self._arch_open)

            arch_sig = tuple((d.index, d.ok, len(d.svg or "")) for d in arch_diagrams)
            if arch_sig != self._rendered_arch_sig:
                self._rendered_arch_sig = arch_sig
                while self.arch_diagrams_lay.count():
                    item = self.arch_diagrams_lay.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()
                for d in arch_diagrams:
                    self.arch_diagrams_lay.addWidget(DiagramBlockWidget(d, self.arch_diagrams_holder))
            self.arch_diagrams_holder.setVisible(bool(arch_diagrams))

            has_draw = bool(parsed.how_to_draw_markdown.strip())
            self.draw_frame.setVisible(has_draw)
            if has_draw:
                self.draw_browser.set_markdown_safe(parsed.how_to_draw_markdown.strip())
        else:
            self.arch_container.setVisible(False)

        # 2. Database design drawer
        has_db = bool(er_diagrams or sql_blocks)
        self.db_toggle_btn.setVisible(has_db)
        if has_db:
            arrow = "▾" if self._db_open else "▸"
            self.db_toggle_btn.setText(f"{arrow} Database design")
            self.db_container.setVisible(self._db_open)

            db_sig = tuple((d.index, d.ok, len(d.svg or "")) for d in er_diagrams)
            if db_sig != self._rendered_db_sig:
                self._rendered_db_sig = db_sig
                while self.db_diagrams_lay.count():
                    item = self.db_diagrams_lay.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()
                for d in er_diagrams:
                    self.db_diagrams_lay.addWidget(DiagramBlockWidget(d, self.db_diagrams_holder))
            self.db_diagrams_holder.setVisible(bool(er_diagrams))

            sql_sig = tuple((b.get("language") or "sql", b.get("code") or "") for b in sql_blocks)
            if sql_sig != self._rendered_sql_sig:
                self._rendered_sql_sig = sql_sig
                while self.db_sql_lay.count():
                    item = self.db_sql_lay.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()
                for b in sql_blocks:
                    self.db_sql_lay.addWidget(
                        CodeSnapWidget(b.get("language") or "sql", b.get("code") or "", self.db_sql_holder)
                    )
            self.db_sql_holder.setVisible(bool(sql_blocks))
        else:
            self.db_container.setVisible(False)

        # 3. Implementation details drawer
        has_impl = bool(impl_md)
        self.impl_toggle_btn.setVisible(has_impl)
        if has_impl:
            arrow = "▾" if self._impl_open else "▸"
            self.impl_toggle_btn.setText(f"{arrow} Implementation details")
            self.impl_container.setVisible(self._impl_open)
            self.tech_browser.set_markdown_safe(impl_md)
        else:
            self.impl_container.setVisible(False)

        # 4. Example code drawer
        code_list = example_blocks if example_blocks else ([] if has_db else sql_blocks)
        has_code = bool(code_list)
        self.code_toggle_btn.setVisible(has_code)
        if has_code:
            arrow = "▾" if self._code_open else "▸"
            self.code_toggle_btn.setText(f"{arrow} Example code")
            self.code_container.setVisible(self._code_open)
            code_sig = tuple((b.get("language") or "python", b.get("code") or "") for b in code_list)
            if code_sig != self._rendered_code_sig:
                self._rendered_code_sig = code_sig
                while self.code_blocks_lay.count():
                    item = self.code_blocks_lay.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()
                for b in code_list:
                    self.code_blocks_lay.addWidget(
                        CodeSnapWidget(b.get("language") or "python", b.get("code") or "", self.code_blocks_holder)
                    )
            self.code_blocks_holder.setVisible(True)
        else:
            self.code_container.setVisible(False)

        doc_sources = [s for s in sources if s.get("source_type") == "document"]
        if doc_sources:
            labels = ", ".join(s.get("label", "Document") for s in doc_sources)
            self.sources_lbl.setText(f"📎 Grounded in workspace documents: {labels}")
            self.sources_lbl.setVisible(True)
        else:
            self.sources_lbl.setVisible(False)

    def _toggle_arch(self) -> None:
        self._arch_open = not self._arch_open
        self.update_card(self.question_text, self.raw_text, self.status, self.sources)

    def _toggle_db(self) -> None:
        self._db_open = not self._db_open
        self.update_card(self.question_text, self.raw_text, self.status, self.sources)

    def _toggle_impl(self) -> None:
        self._impl_open = not self._impl_open
        self.update_card(self.question_text, self.raw_text, self.status, self.sources)

    def _toggle_code(self) -> None:
        self._code_open = not self._code_open
        self.update_card(self.question_text, self.raw_text, self.status, self.sources)

    def _copy_spoken(self) -> None:
        if self._parsed:
            QGuiApplication.clipboard().setText(self._parsed.spoken_clean or self._parsed.spoken_markdown)
            self.copy_spoken_btn.setText("✓ Copied answer")
            QTimer.singleShot(1400, lambda: self.copy_spoken_btn.setText("📋 Copy answer"))

    def _copy_full(self) -> None:
        clean_full = re.sub(r"```design-state[\s\S]*?```", "", self.raw_text, flags=re.IGNORECASE).strip()
        QGuiApplication.clipboard().setText(clean_full)
        self.copy_full_btn.setText("✓ Copied full")
        QTimer.singleShot(1400, lambda: self.copy_full_btn.setText("📄 Copy full"))

    def _copy_code(self) -> None:
        if self._parsed and self._parsed.code_blocks:
            joined = "\n\n".join(b["code"] for b in self._parsed.code_blocks)
            QGuiApplication.clipboard().setText(joined)
            self.copy_code_btn.setText("✓ Copied Code")
            QTimer.singleShot(1400, lambda: self.copy_code_btn.setText("📋 Copy Code"))


class AnswerView(QWidget):
    """Single-scroll-area list of structured AI answer cards with expand/restore toggle."""

    expand_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._answers: dict[str, dict] = {}
        self._order: list[str] = []
        self._cards: dict[str, AnswerCardWidget] = {}
        self._pinned_to_bottom: bool = True
        self._spoken_font_px: int = 15

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(4, 2, 4, 2)
        hdr_lbl = QLabel("<b>AI Answers</b>")
        hdr_lbl.setStyleSheet("font-size:13.5px;color:#0f172a;border:none;")
        self.count_lbl = QLabel("0 answers")
        self.count_lbl.setStyleSheet("font-size:11.5px;color:#64748b;border:none;")

        self.expand_btn = QPushButton("◨ Expand Answer")
        self.expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_btn.setToolTip("Expand AI Answers panel or restore balanced split")
        self.expand_btn.setStyleSheet(
            "QPushButton{font-size:11px;font-weight:600;padding:2px 8px;background:#f1f5f9;color:#1e293b;border:1px solid #cbd5e1;border-radius:4px;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )
        self.expand_btn.clicked.connect(self.expand_requested.emit)

        hdr_row.addWidget(hdr_lbl)
        hdr_row.addSpacing(6)
        hdr_row.addWidget(self.count_lbl)
        hdr_row.addStretch()
        hdr_row.addWidget(self.expand_btn)
        layout.addLayout(hdr_row)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("AnswerScrollArea")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea#AnswerScrollArea{background:#f8fafc;border:1px solid #cbd5e1;border-radius:8px;}"
        )
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)

        self._container = QWidget()
        self._container.setStyleSheet("background:#f8fafc;")
        self._cards_layout = QVBoxLayout(self._container)
        self._cards_layout.setContentsMargins(8, 8, 8, 8)
        self._cards_layout.setSpacing(10)

        self._empty_lbl = QLabel(
            "Select a transcript paragraph's <b>Send</b> button or type a question below to get a natural spoken answer, architecture diagram, or database design."
        )
        self._empty_lbl.setWordWrap(True)
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setStyleSheet("color:#64748b;font-size:13px;padding:28px;border:none;")
        self._cards_layout.addWidget(self._empty_lbl)
        self._cards_layout.addStretch(1)

        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll, 1)

        self.jump_btn = QPushButton("↓ Jump to latest answer")
        self.jump_btn.setStyleSheet(
            "background:#1e40af;color:#ffffff;font-weight:bold;font-size:11.5px;padding:5px 12px;border-radius:12px;"
        )
        self.jump_btn.setVisible(False)
        self.jump_btn.clicked.connect(self.scroll_to_bottom)
        layout.addWidget(self.jump_btn, 0, Qt.AlignmentFlag.AlignCenter)

    def set_expanded_label(self, is_expanded: bool) -> None:
        self.expand_btn.setText("❐ Restore Split" if is_expanded else "◨ Expand Answer")

    def _on_scroll_changed(self, value: int) -> None:
        bar = self._scroll.verticalScrollBar()
        at_bottom = (bar.maximum() - value) <= 55
        self._pinned_to_bottom = at_bottom
        if at_bottom:
            self.jump_btn.setVisible(False)

    def _any_card_has_selection(self) -> bool:
        return any(card.has_user_selection() for card in self._cards.values())

    def scroll_to_bottom(self) -> None:
        self._pinned_to_bottom = True
        self.jump_btn.setVisible(False)
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _entry(self, aid: str) -> dict:
        if aid not in self._answers:
            self._answers[aid] = {"question": "", "text": "", "sources": [], "status": "streaming"}
            self._order.append(aid)
        return self._answers[aid]

    def load_snapshot(self, answers: list[dict]) -> None:
        self._answers.clear()
        self._order.clear()
        for card in self._cards.values():
            card.deleteLater()
        self._cards.clear()

        for a in answers:
            aid = str(a["id"])
            self._answers[aid] = {
                "question": a.get("question_text", ""),
                "text": a.get("answer_text", ""),
                "sources": a.get("sources", []),
                "status": a.get("status", "completed"),
            }
            self._order.append(aid)
        self._sync_all_cards(force_scroll=True)

    def handle_event(self, kind: str, data: dict) -> None:
        aid = data.get("answer_id")
        if not aid:
            return
        aid = str(aid)
        e = self._entry(aid)
        is_new = kind == "answer.started"
        if kind == "answer.started":
            e["question"] = data.get("question_text", "")
            e["status"] = "streaming"
        elif kind == "answer.sources":
            e["sources"] = data.get("sources", [])
        elif kind == "answer.delta":
            delta, have = data.get("delta", ""), len(e["text"])
            off = data.get("offset", have)
            if off + len(delta) <= have:
                return  # duplicate (already have this text)
            if off > have:
                e["text"] += ""  # gap: keep what we have; the final answer.completed text replaces it
            else:
                e["text"] += delta[have - off:]
        elif kind == "answer.diagram":
            pass
        elif kind == "answer.completed":
            e["text"] = data.get("answer_text", e["text"])
            e["status"] = "completed"
            if data.get("total_ms"):
                e["timing"] = f" · {data['total_ms'] / 1000:.1f}s (first words {(data.get('first_word_ms') or 0) / 1000:.1f}s)"
        elif kind == "answer.failed":
            e["status"] = "failed"
        self._sync_single_card(aid, is_new=is_new)

    def _adjust_font_size(self, delta: int) -> None:
        self._spoken_font_px = max(12, min(22, self._spoken_font_px + delta))
        for card in self._cards.values():
            card.set_spoken_font_px(self._spoken_font_px)

    def _ensure_card_widget(self, aid: str) -> AnswerCardWidget:
        if aid not in self._cards:
            card = AnswerCardWidget(aid, spoken_font_px=self._spoken_font_px, parent=self._container)
            card.font_delta_requested.connect(self._adjust_font_size)
            self._cards[aid] = card
            insert_idx = max(0, self._cards_layout.count() - 1)
            self._cards_layout.insertWidget(insert_idx, card)
        return self._cards[aid]

    def _sync_single_card(self, aid: str, is_new: bool = False) -> None:
        self._empty_lbl.setVisible(len(self._order) == 0)
        n = len(self._order)
        self.count_lbl.setText(f"{n} answer{'s' if n != 1 else ''}")

        a = self._answers[aid]
        card = self._ensure_card_widget(aid)
        card.update_card(a["question"], a["text"], a["status"], a["sources"], a.get("timing", ""))

        if (self._pinned_to_bottom or is_new) and not self._any_card_has_selection():
            QTimer.singleShot(10, self.scroll_to_bottom)
        elif not self._pinned_to_bottom:
            self.jump_btn.setVisible(True)

    def _sync_all_cards(self, force_scroll: bool = False) -> None:
        self._empty_lbl.setVisible(len(self._order) == 0)
        n = len(self._order)
        self.count_lbl.setText(f"{n} answer{'s' if n != 1 else ''}")
        for aid in self._order:
            a = self._answers[aid]
            card = self._ensure_card_widget(aid)
            card.update_card(a["question"], a["text"], a["status"], a["sources"], a.get("timing", ""))
        if force_scroll:
            QTimer.singleShot(20, self.scroll_to_bottom)

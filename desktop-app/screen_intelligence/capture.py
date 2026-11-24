"""Screen capture with Qt only (no extra dependency): pick a monitor, grab it, return a compact JPEG."""
from __future__ import annotations

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QScreen

AUTO = "auto"
MAX_WIDTH = 1920   # enough to read code; keeps the upload around a few hundred KB
JPEG_QUALITY = 85


def list_screens() -> list[tuple[str, str]]:
    """[(key, label)] for every monitor, e.g. ("screen:0", "Display 1 (1920x1080)")."""
    out = []
    for i, s in enumerate(QGuiApplication.screens()):
        g = s.geometry()
        out.append((f"screen:{i}", f"Display {i + 1} ({g.width()}x{g.height()})"))
    return out


def pick_screen(key: str, window_screen: QScreen | None) -> QScreen:
    """AUTO = a monitor other than the one showing this app (that is where the question usually is); with a single
    monitor it is that monitor."""
    screens = QGuiApplication.screens()
    if key.startswith("screen:"):
        try:
            return screens[int(key.split(":")[1])]
        except (ValueError, IndexError):
            pass
    others = [s for s in screens if s is not window_screen]
    return others[0] if others else (window_screen or QGuiApplication.primaryScreen())


def grab_jpeg(screen: QScreen, max_width: int = MAX_WIDTH, quality: int = JPEG_QUALITY) -> bytes:
    image = screen.grabWindow(0).toImage()
    if image.isNull():
        raise RuntimeError("The screen could not be captured.")
    if image.width() > max_width:
        image = image.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buf, "JPEG", quality):
        raise RuntimeError("The screenshot could not be encoded.")
    return bytes(buf.data())

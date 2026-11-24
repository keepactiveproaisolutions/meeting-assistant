"""QR code rendering for the meeting join URL."""
import io

import qrcode
from PySide6.QtGui import QPixmap


def qr_pixmap(data: str, box_size: int = 6) -> QPixmap:
    img = qrcode.make(data, box_size=box_size, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    pix = QPixmap()
    pix.loadFromData(buf.getvalue(), "PNG")
    return pix

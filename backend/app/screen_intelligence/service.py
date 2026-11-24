"""Transient screenshot hand-off between the ask endpoint and the answer runner.

Screenshots are kept in memory only (never written to disk or the database) and removed as soon as the answer
starts. They are sent to the model as an image part of the final user message.
"""
import base64
import time

from app.screen_intelligence.prompt import SCREEN_INSTRUCTIONS

MAX_IMAGE_BYTES = 8 * 1024 * 1024
_TTL_S = 300
_pending: dict[str, tuple[float, bytes, str]] = {}


def detect_mime(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def put_image(answer_id, data: bytes, mime: str) -> None:
    now = time.monotonic()
    for k in [k for k, (t, _, _) in _pending.items() if now - t > _TTL_S]:
        _pending.pop(k, None)
    _pending[str(answer_id)] = (now, data, mime)


def take_image(answer_id) -> tuple[bytes, str] | None:
    hit = _pending.pop(str(answer_id), None)
    return (hit[1], hit[2]) if hit else None


def attach_image(messages: list[dict], image: tuple[bytes, str]) -> list[dict]:
    """Return messages whose LAST user message also carries the screenshot and the screen-mode instructions."""
    data, mime = image
    url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    out = [dict(m) for m in messages]
    last = out[-1]
    last["content"] = [
        {"type": "text", "text": f"{last['content']}\n\n{SCREEN_INSTRUCTIONS}"},
        {"type": "image_url", "image_url": {"url": url, "detail": "high"}},
    ]
    return out

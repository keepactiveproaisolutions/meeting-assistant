"""Re-exports the shared pure-Python diagram renderer and structured answer parser from backend."""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.services.diagram_renderer import (  # noqa: E402, F401
    DiagramResult,
    StructuredAnswer,
    parse_structured_answer,
    render_mermaid,
    sanitize_markdown_for_qt,
)

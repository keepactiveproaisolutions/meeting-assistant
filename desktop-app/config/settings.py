"""Desktop settings (env vars or desktop-app/.env). No secrets live here: OpenAI is backend-only."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@dataclass(frozen=True)
class Settings:
    api_url: str = os.getenv("MA_API_URL", "http://localhost:8000").rstrip("/")
    web_url: str = os.getenv("MA_WEB_URL", "http://localhost:5173").rstrip("/")
    chunk_seconds: float = float(os.getenv("MA_CHUNK_SECONDS", "2"))
    target_sample_rate: int = 16_000  # what the transcription model wants
    # Microphone capture is intentionally NOT supported; system audio only.

    @property
    def ws_url(self) -> str:
        return self.api_url.replace("https://", "wss://").replace("http://", "ws://")


settings = Settings()

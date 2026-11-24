"""Application settings loaded from environment / .env."""
from functools import lru_cache
import ipaddress
from pathlib import Path
import socket
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = str(Path(__file__).resolve().parents[2] / ".env")


def detect_private_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            # UDP connect sends no packets; it only picks the default-route adapter
            # (real Wi-Fi/LAN), not virtual adapters that match private ranges.
            sock.connect(("8.8.8.8", 80))
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


def _is_permitted_dev_url(url: str, allow_lan: bool) -> bool:
    if not url or url == "*":
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    if allow_lan:
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_private or ip.is_loopback
        except ValueError:
            return False
    return False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    app_name: str = "Meeting Assistant API"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/meeting_assistant"

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720

    openai_api_key: str
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_transcription_model: str = "whisper-1"
    openai_reasoning_effort: str = ""   # none|minimal|low|medium|high; empty = model default
    transcription_language: str = "en"  # ISO-639-1; empty = auto-detect (prone to wrong-language output on short clips)
    embedding_dimensions: int = 1536

    cors_origins: str = "http://localhost:5173"
    web_app_url: str = "http://localhost:5173"
    upload_dir: str = "uploads"
    max_upload_mb: int = 25

    rag_top_k: int = 5
    rag_transcript_max_chars: int = 8000
    chunk_size: int = 1000
    chunk_overlap: int = 150

    dev_auth_bypass: bool = False
    allow_lan_dev: bool = False
    llm_mock: bool = False
    environment: str = "development"

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if self.allow_lan_dev:
            lan_ip = detect_private_lan_ip()
            if lan_ip:
                lan_origin = f"http://{lan_ip}:5173"
                if lan_origin not in origins:
                    origins.append(lan_origin)
        return origins

    def validate_safety(self) -> None:
        is_dev_env = self.environment.lower() in ("development", "local", "dev", "test")
        if self.llm_mock and not is_dev_env:
            raise RuntimeError("LLM_MOCK cannot be enabled outside local development environments!")
        if self.dev_auth_bypass:
            if not is_dev_env:
                raise RuntimeError("DEV_AUTH_BYPASS cannot be enabled outside local development environments!")
            for origin in self.cors_origin_list:
                if not _is_permitted_dev_url(origin, self.allow_lan_dev):
                    raise RuntimeError(f"DEV_AUTH_BYPASS is not permitted with non-local CORS origin: {origin}")
            if not _is_permitted_dev_url(self.web_app_url, self.allow_lan_dev):
                raise RuntimeError(f"DEV_AUTH_BYPASS is not permitted with non-local WEB_APP_URL: {self.web_app_url}")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

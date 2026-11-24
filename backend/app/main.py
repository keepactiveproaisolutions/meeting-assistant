"""FastAPI application entrypoint."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import auth, documents, meetings, profile, workspaces, ws
from app.core.config import get_settings
from app.screen_intelligence.router import router as screen_router

settings = get_settings()

_STARTUP_DDL = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_sha256 VARCHAR(64)",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_kind VARCHAR(40) NOT NULL DEFAULT 'document'",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extracted_text TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(100)",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedding_status VARCHAR(40) NOT NULL DEFAULT 'pending_approval'",
    "CREATE INDEX IF NOT EXISTS ix_documents_file_sha256 ON documents (file_sha256)",
    "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS section_title VARCHAR(200)",
    "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(100)",
    "ALTER TABLE document_chunks ALTER COLUMN embedding DROP NOT NULL",
    "ALTER TABLE profile_entries ADD COLUMN IF NOT EXISTS cv_document_id UUID REFERENCES documents(id) ON DELETE SET NULL",
    "ALTER TABLE profile_entries ADD COLUMN IF NOT EXISTS self_reported_skills TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE profile_entries ADD COLUMN IF NOT EXISTS demo_prep_json TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS meta_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS document_ids_json TEXT",
    "UPDATE documents SET doc_kind = 'supporting' WHERE doc_kind = 'document'",
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_safety()
    upload_root = Path(__file__).resolve().parents[1] / settings.upload_dir
    upload_root.mkdir(parents=True, exist_ok=True)
    from app.core.database import Base, engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _STARTUP_DDL:
            await conn.execute(text(stmt))
    if settings.dev_auth_bypass:
        from app.core.database import SessionLocal
        from app.core.dev_auth import ensure_dev_identity
        async with SessionLocal() as db:
            await ensure_dev_identity(db)
    # Documents left mid-ingestion by a previous process are re-queued (never silently left "processing").
    from app.services.document_service import resume_interrupted_ingestion
    await resume_interrupted_ingestion()
    yield
    # Shutdown: close every paid Live stream (bounded finalization) so no OpenAI session is orphaned.
    from app.services import live_transcription
    for sess in list(live_transcription.sessions.values()):
        try:
            await sess.finish(None)
        except Exception:  # noqa: BLE001
            pass


import logging as _logging
for _name in ("live", "timing"):  # measured latency / usage lines go to the server log
    _lg = _logging.getLogger(_name)
    _lg.setLevel(_logging.INFO)
    if not _lg.handlers:
        _h = _logging.StreamHandler()
        _h.setFormatter(_logging.Formatter("%(asctime)s.%(msecs)03d %(name)s %(message)s", "%H:%M:%S"))
        _lg.addHandler(_h)

app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

for r in (auth.router, workspaces.router, documents.router, meetings.router, profile.router, screen_router):
    app.include_router(r, prefix="/api/v1")
app.include_router(ws.router)  # /ws/meetings/{id}


@app.get("/health")
async def health():
    return {"status": "ok"}

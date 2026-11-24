# Backend (FastAPI)

Folders: `app/api` routers · `app/services` business logic · `app/models` ORM · `app/schemas` Pydantic · `app/rag` retrieval/prompting · `app/realtime` WebSocket hub · `app/core` config/security/deps · `migrations/` Alembic · `uploads/` stored documents.

## Windows setup
```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # then edit: DATABASE_URL, JWT_SECRET, OPENAI_API_KEY
```
Create the database (psql): `CREATE DATABASE meeting_assistant;` — pgvector must be installed for your PostgreSQL version (`CREATE EXTENSION vector;` is run by the migration).

```powershell
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Open http://localhost:8000/docs. Health check: `/health`.

## Notes
- OpenAI is called only here; clients never see the key.
- Allow inbound TCP 8000 in Windows Firewall if the desktop/web run on other machines.
- Models used (configurable in `.env`): chat `gpt-4o-mini`, embeddings `text-embedding-3-small` (1536 dims), transcription `gpt-4o-mini-transcribe`.
- Uploaded files: PDF, DOCX, TXT, MD (max `MAX_UPLOAD_MB`). Ingestion status is visible via `GET /api/v1/workspaces/{id}/documents`.

# Meeting Assistant

Real-time AI meeting assistant: a Windows desktop app captures **system audio only** (WASAPI loopback), the FastAPI backend transcribes it, detects questions and answers them with RAG over your uploaded documents (streamed from OpenAI), and a React web app joins the same live session by code or QR.

| Folder | Purpose |
|---|---|
| [`desktop-app/`](desktop-app/README.md) | PySide6 Windows app: loopback capture, live transcript, ask UI, meeting code + QR |
| [`backend/`](backend/README.md) | FastAPI, PostgreSQL + pgvector, WebSocket sync, RAG, OpenAI (server-side only) |
| [`web-app/`](web-app/README.md) | React + Vite client to join and follow/ask in a live meeting |
| [`docs/`](docs/) | Architecture, database and API design |

## Installation (Windows)
Prerequisites: Windows 10/11, Python 3.12, Node.js 20+, PostgreSQL 15+ with [pgvector](https://github.com/pgvector/pgvector#windows), an OpenAI API key.

1. **Database** – create the database `meeting_assistant` (the pgvector extension is created automatically; the database user needs permission to `CREATE EXTENSION`).
2. **Setup** – run `setup.bat` once. It creates the virtual environments (`backend\.venv`, `desktop-app\.venv`), installs every dependency from the requirements files, runs `npm install` for the web app and creates `backend\.env` from the template.
3. **Configure** – open `backend\.env` and set `OPENAI_API_KEY` and `DATABASE_URL`.
4. **Run** – start everything with `start-all.bat`, or individually with `start-backend.bat`, `start-web.bat` and `launch-desktop.bat`.
5. Open http://localhost:5173, register, upload your documents, then press **Start meeting** in the desktop app. Open the web app and enter the code or scan the QR.

Dependency files: [`requirements.txt`](requirements.txt) (overview), [`backend/requirements.txt`](backend/requirements.txt), [`backend/requirements-dev.txt`](backend/requirements-dev.txt) (test tools), [`desktop-app/requirements.txt`](desktop-app/requirements.txt), plus a `requirements-lock.txt` next to each for exact, reproducible installs.

### Troubleshooting
| Message | Cause and fix |
|---|---|
| `No module named 'app'` | The backend was started outside `backend\`. Use `start-backend.bat`, or `cd backend` first and run `.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000`. |
| `No module named 'main'` / `main.app` | The desktop app was started outside `desktop-app\` or with the backend's venv. Use `launch-desktop.bat`, or `cd desktop-app` and run `.venv\Scripts\python.exe -m main.app`. |
| `Could not read package.json` | `npm` was run outside `web-app\`. Use `start-web.bat`, or `cd web-app` first. From the project root, `npm run dev` also works. |
| Anything about missing packages | Run `setup.bat` again – it is safe to repeat. |

Each part has its own virtual environment, so always use the interpreter inside that part's `.venv` (the scripts do this for you). `launch-desktop.bat` runs without a console window; if the app does not appear, check `desktop-app\app.log`.

### Manual installation
```
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
python -m venv desktop-app\.venv
desktop-app\.venv\Scripts\python.exe -m pip install -r desktop-app\requirements.txt
cd web-app && npm install
```

## AI Screen Intelligence
The **🖥 Screen** button in the desktop app reads the screen and answers questions, coding problems and errors; **📺 Live** shows a live preview of the screen inside the same window (same header, footer and session). See [docs/screen-intelligence.md](docs/screen-intelligence.md).

## Status
Implemented: backend (models, migration, auth, workspaces, documents + ingestion, meetings, audio transcription, question detection, RAG, streaming WS), desktop capture + UI, web live-sync UI. Not included: document-management UI (use `/docs` for now), mobile app, automated tests.

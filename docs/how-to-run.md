# How to run

Three parts run together: **backend** (port 8000), **web app** (port 5173) and the **desktop app** (Windows).

## Requirements
- Windows 10/11, Python 3.12, Node.js 18+
- PostgreSQL with the `pgvector` extension (`install_pgvector.bat` helps on Windows)
- An OpenAI API key

## 1. One-time setup
```bat
setup.bat
```
Creates `backend\.venv` and `desktop-app\.venv`, installs all packages and creates `backend\.env`.

Then open `backend\.env` and set:
```
DATABASE_URL=postgresql+asyncpg://postgres:<password>@localhost:5432/meeting_assistant
OPENAI_API_KEY=<your key>
JWT_SECRET=<long random string>
```
Create the database once, then apply the schema:
```bat
cd backend
.venv\Scripts\python.exe -m alembic upgrade head
```

## 2. Start
```bat
start-all.bat
```
This opens the backend and web app in their own windows and then the desktop app.

Or start each part on its own:

| Part | Command | Address |
|---|---|---|
| Backend | `start-backend.bat` | http://localhost:8000 (docs at `/docs`) |
| Web app | `start-web.bat` | http://localhost:5173 |
| Desktop | `launch-desktop.bat` | Windows app |

## 3. Use it
1. Open http://localhost:5173, register and upload your documents (CV, notes, any file).
2. In the desktop app, sign in and press **Start meeting**.
3. Phone or web: open the web address (on your Wi-Fi use `http://<PC-IP>:5173`), enter the meeting code or scan the QR. All devices share one session.
4. Ask by speaking, typing, or the **Screen** button (Ctrl+Shift+S) to ask about what is on your monitor. **Live Screen** shows a preview inside the same window.

## Tests
```bat
backend\.venv\Scripts\python.exe backend\tests\test_documents_and_grounding.py
backend\.venv\Scripts\python.exe backend\tests\live_multiplatform_session.py
```

## Common problems
| Message | Fix |
|---|---|
| `No module named 'app'` / `'main'` | Start from the project scripts above, not from the wrong folder. |
| `package.json` not found | Run web commands inside `web-app\` or use `start-web.bat`. |
| Script "not recognized" | Run `.\setup.bat` (cmd does not search the current folder). |
| Database connection error | Check `DATABASE_URL` and that PostgreSQL is running. |
| Phone cannot open the link | Same Wi-Fi, and allow ports 5173 and 8000 in Windows Firewall. |

# Desktop app (Windows)

PySide6 app. Folders: `main/` entrypoint + controller · `audio/` WASAPI loopback capture · `transcription/` chunk uploader · `ai/` REST + WebSocket clients · `ui/` widgets · `config/` settings.

Captures **system audio only** (loopback of the default output device) in 2-second chunks; the microphone is never opened.

## Windows setup
```powershell
cd desktop-app
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# optional: create desktop-app\.env
#   MA_API_URL=http://localhost:8000
#   MA_WEB_URL=http://<your-LAN-IP>:5173   # so the QR code works from other devices
python -m main.app
```
1. Sign in with the account you registered (first workspace is used).
2. **Start meeting** → code + QR appear; capture begins. Play audio/join a call and the transcript fills in.
3. Questions are highlighted; click **Ask AI**, or type a question / select transcript text and press **Send**. Answers stream on the right with sources and timestamps.
4. **Copy** per line, **Copy All** in the header.

## Troubleshooting
- "No WASAPI loopback device": make sure a playback device is enabled and set as default in Windows Sound settings.
- No transcript: audio must actually be playing (silent chunks are skipped); check the backend log and `OPENAI_API_KEY`.
- Phone/other PC can't open QR link: set `MA_WEB_URL` (desktop) and `WEB_APP_URL` (backend) to your LAN IP and allow ports 5173/8000 in the firewall.

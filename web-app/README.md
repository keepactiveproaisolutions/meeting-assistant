# Web app (React + Vite)

Folders: `src/components` panels · `src/pages` Login/Join/Meeting · `src/services` API client · `src/hooks` WebSocket state · `src/styles` CSS · `public/` static assets.

Joins a live meeting via code or QR (`/join?code=XXXXXXXX`), shows live transcript, detected questions and streamed AI answers, and lets you copy/select transcript text and send questions. It does **not** capture audio.

## Windows setup
```powershell
cd web-app
copy .env.example .env     # VITE_API_URL=http://localhost:8000
npm install
npm run dev                # http://localhost:5173
```
Build for production: `npm run build` (output in `dist/`). Add your deployed origin to the backend `CORS_ORIGINS`.

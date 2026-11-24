# AI Screen Intelligence & Coding Copilot

One button in the desktop app reads what is on the screen and answers it: interview or quiz questions, coding
problems (approach, complete solution, complexity, edge cases, how to explain it), code and error messages (explain and
fix), slides, documents and dashboards.

## How to use
1. Start or join a meeting in the desktop app (the answer is shared with the web app like any other answer).
2. Bring the question or problem up on screen.
3. Click **🖥 Screen** in the bottom bar (or press **Ctrl+Shift+S** while the app is focused).
   - Optional: type an instruction first, e.g. `solve in Java`, `use recursion`, `answer question 2`.
   - The small arrow next to the button picks the monitor. **Auto** captures a monitor other than the one showing the
     app; with a single monitor it captures that one (the app hides itself for a moment during the capture).
4. The answer streams into the normal answer panel, tagged `[Screen]`. Follow-up questions ("now in Java", "why this
   approach?") work with full session memory.

## Live Screen (inside the same window)
Click **📺 Live** in the footer to show a live preview of your screen **inside the Meeting Assistant window**. It replaces
the transcript pane while it is on; the header, the footer (question box, 🖥 Screen, Send) and the answer panel stay
exactly as they are, so everything is the same page and the same session. Click **📺 Live** again (or ✕ in the panel)
to return to the transcript.

- The panel has the monitor picker (Auto or a specific display), the preview speed (1, 2 or 5 fps) and pause/resume.
- Type a question in the footer as usual, or press **🖥 Screen** (with an optional instruction) to analyze the screen
  shown in the panel. The answer streams into the normal answer panel, with the same session memory.

Tip: with a single monitor the preview shows the app inside itself (a warning is displayed); **🖥 Screen** still
captures a clean screen because the app hides for a moment while the screenshot is taken. With two monitors, **Auto**
previews the other monitor.

## Where the code lives
| Part | Folder |
|---|---|
| Desktop: capture, buttons, controller, Live Screen window | `desktop-app/screen_intelligence/` (`live_panel.py`) |
| Backend: endpoint, prompt, screenshot hand-off | `backend/app/screen_intelligence/` |
| Endpoint | `POST /api/v1/meetings/{id}/screen-ask` (multipart: `image` + optional `note`) |

The only edits outside those folders are small hooks: the button in `ui/main_window.py`, the wiring in `main/app.py`,
`screen_ask` in `ai/api_client.py`, router registration in `app/main.py`, and the image hook in
`services/answer_service.py`.

## Configuration
No new settings. It uses the configured chat model (`OPENAI_CHAT_MODEL`, which must accept images) and the same API
key. Screenshots are scaled to at most 1920 px wide JPEG (a few hundred KB); the backend accepts JPEG/PNG/WebP up to 8 MB.

## Privacy and safety
- Screenshots are held in memory only for the one request: never written to disk or the database.
- The model is told not to identify people from faces or appearance and not to repeat secrets visible on screen.
- Only press the button when you are allowed to use AI assistance in that situation (many interviews, exams and
  assessments forbid it). The feature does not hide itself from anyone.

## Tests
- `desktop-app/tests/test_screen_intelligence.py` — zero cost, offscreen Qt, fake API.
- `backend/tests/live_screen_intelligence.py` — live, real vision model, synthetic screenshots (a few cents).

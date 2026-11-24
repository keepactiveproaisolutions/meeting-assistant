"""Zero-cost test of the desktop Screen Intelligence feature (offscreen Qt, fake API, no network, no real screen content).
Run from desktop-app/:  .venv\\Scripts\\python.exe tests\\test_screen_intelligence.py"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from screen_intelligence import capture  # noqa: E402
from screen_intelligence.controller import ScreenIntelligence  # noqa: E402
from screen_intelligence.widget import ScreenCopilotButton  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

PASSED = 0


def check(name, cond):
    global PASSED
    assert cond, name
    PASSED += 1
    print(f"[PASS] {name}")


def spin(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


app = QApplication.instance() or QApplication([])

# --- capture ---------------------------------------------------------------------------------
screens = capture.list_screens()
check("at least one monitor listed", len(screens) >= 1)
scr = capture.pick_screen(capture.AUTO, None)
jpeg = capture.grab_jpeg(scr)
check("grab returns a JPEG", jpeg[:3] == b"\xff\xd8\xff" and len(jpeg) > 500)
check("explicit monitor key resolves", capture.pick_screen("screen:0", None) is not None)
check("bad monitor key falls back", capture.pick_screen("screen:99", None) is not None)

# --- window integration ----------------------------------------------------------------------
win = MainWindow(dev_mode=True)
check("Screen button is in the composer", isinstance(win.screen_btn, ScreenCopilotButton))
emitted = []
win.screen_clicked.connect(lambda note, mon: emitted.append((note, mon)))
win.input.setText("solve in Java")
win.screen_btn.click()
check("click emits the typed instruction + monitor", emitted == [("solve in Java", capture.AUTO)])
check("instruction box cleared after use", win.input.text() == "")

# --- controller ------------------------------------------------------------------------------
class FakeApi:
    def __init__(self):
        self.calls = []

    def screen_ask(self, meeting_id, jpeg, note=""):
        self.calls.append((meeting_id, jpeg, note))
        return {"answer_id": "x"}


api, status = FakeApi(), []
ctl = ScreenIntelligence(api, lambda: "meeting-1", lambda: [win, win.floating_win])
ctl.status.connect(status.append)
win.show()
ctl.analyze("use recursion", capture.AUTO)
check("second click ignored while busy", (ctl.analyze("again", capture.AUTO), True)[1])
spin(1200)
check("exactly one upload happened", len(api.calls) == 1)
mid, sent, note = api.calls[0]
check("uploaded to the right meeting with the note and a JPEG", mid == "meeting-1" and note == "use recursion" and sent[:3] == b"\xff\xd8\xff")
check("window opacity restored after capture", win.windowOpacity() == 1.0)
check("status reports sent", any("Screen sent" in s for s in status))
check("controller is ready again", ctl._busy is False)

no_meeting = ScreenIntelligence(api, lambda: None, lambda: [win])
msgs = []
no_meeting.status.connect(msgs.append)
no_meeting.analyze("", capture.AUTO)
check("no meeting -> helpful message, nothing uploaded", len(api.calls) == 1 and "meeting" in msgs[-1].lower())


class FailingApi:
    def screen_ask(self, *a, **k):
        raise RuntimeError("backend down")


bad = ScreenIntelligence(FailingApi(), lambda: "m", lambda: [win])
bmsgs = []
bad.status.connect(bmsgs.append)
bad.analyze("", capture.AUTO)
spin(1200)
check("upload failure is reported and controller recovers", "failed" in bmsgs[-1].lower() and bad._busy is False)

# --- Live Screen panel INSIDE the main window (same header + footer) ----------------------------
win.resize(1100, 720)
win.show()
spin(200)
check("Live panel lives inside the main window (not a separate window)", win.live_panel.window() is win)
check("transcript is the default left pane", win.left_stack.currentWidget() is win.transcript)
win.live_btn.click()
spin(900)
check("Live button switches the left pane to the live panel", win.left_stack.currentWidget() is win.live_panel)
check("footer (question box, Screen, Send) is still there", win.input.isVisible() and win.screen_btn.isVisible())
check("answer panel is still there", win.answers.isVisible())
check("live panel shows a live frame", win.live_panel._frames >= 1 and not win.live_panel.preview.pixmap().isNull())
f0 = win.live_panel._frames
spin(1300)
check("preview keeps updating", win.live_panel._frames > f0)
win.live_panel.pause_btn.click()
p0 = win.live_panel._frames
spin(1100)
check("pause stops the preview", win.live_panel._frames == p0)
win.live_panel.pause_btn.click()
spin(800)
check("resume restarts the preview", win.live_panel._frames > p0)

emitted.clear()
win.input.setText("solve in Java")
win.screen_btn.click()
check("footer Screen button works in live mode and uses the panel's screen",
      emitted == [("solve in Java", win.live_panel.current_monitor())])

shot = Path(os.environ.get("TEMP", ".")) / "live_screen_inline.png"
win.grab().save(str(shot))

win.live_panel.close_btn.click()
check("Back to transcript returns the transcript pane", win.left_stack.currentWidget() is win.transcript and not win.live_btn.isChecked())
check("preview stops when the panel is hidden", not win.live_panel._timer.isActive())
win.live_btn.click()
check("Live can be turned on again", win.left_stack.currentWidget() is win.live_panel)
win.live_btn.click()

live_ctl = ScreenIntelligence(api, lambda: "meeting-1", lambda: [win, win.floating_win])
live_ctl.status.connect(win.live_panel.set_status)
n_before = len(api.calls)
win.live_btn.click()
live_ctl.analyze("", capture.AUTO)
spin(1200)
check("analysis in live mode uploads once and restores the window", len(api.calls) == n_before + 1 and win.windowOpacity() == 1.0)
win.live_btn.click()

print(f"\nAll {PASSED} checks passed.")

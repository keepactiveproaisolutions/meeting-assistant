"""Deterministic UI test (no network, no cost) of inline Live text in the real TranscriptView.
Replays the backend's event order: partial updates -> utterance (final) -> partial clear -> next turn, plus a long
pause (new card), scroll-up (no forced follow), snapshot reload with an in-flight partial (reconnect / history).
Saves screenshots to <outdir>.  Usage: python tests/test_inline_live_ui.py <outdir>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.transcript_view import TranscriptView  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT.mkdir(parents=True, exist_ok=True)
app = QApplication.instance() or QApplication(sys.argv)
R = []


def check(name, cond):
    R.append(bool(cond))
    print(("[PASS] " if cond else "[FAIL] ") + name)


def spin(n=6):
    for _ in range(n):
        app.processEvents()


def body_text(row):
    return row.body.text()


v = TranscriptView()
v.resize(620, 520)
v.show()
spin()
u1 = {"id": "u1", "start_offset_ms": 0, "end_offset_ms": 6000, "is_final": True,
      "text": "Thanks for joining. Our client is migrating historical data into Amazon Redshift."}
v.add_utterance(u1)
spin()

# --- turn 2 continues the same paragraph (pause < 1.5 s): provisional tail grows in the SAME card
for t in ["How would", "How would you design", "How would you design incremental loads"]:
    v.set_partial("item2", t, 6800, False)
    spin()
row1 = v._paragraphs["u1"]
check("provisional words shown inline as tail of latest card", row1.tail == "How would you design incremental loads")
check("no separate pending card / bottom strip", v._pending_row is None and not hasattr(v, "provisional"))
check("finalized text unchanged while tail updates", row1.text == u1["text"])
check("Send enabled (card has finalized text)", row1.ask_btn.isEnabled())
v.grab().save(str(OUT / "after_1_inline_tail.png"))

sent = []
v.ask_requested.connect(lambda text, uid: sent.append(text))
row1._on_ask()
check("Send submits only the finalized paragraph (not provisional words)", sent == [u1["text"]])

# --- finalization, in backend order: utterance update THEN partial clear
u1b = dict(u1, end_offset_ms=10500, is_final=True,
           text=u1["text"] + " How would you design incremental loads for hourly tables?")
v.add_utterance(u1b)
spin()
check("final text replaces provisional in place (tail removed in the same update)",
      row1.tail == "" and row1.text == u1b["text"])
check("finalized text appears once", body_text(row1).count("incremental loads") == 1)
v.set_partial("item2", "", 6800, True)
spin()
check("still one card after partial clear", len(v._paragraphs) == 1 and v._pending_row is None)

# --- long pause (> 1.5 s): next turn opens a pending card in the same layout, Send disabled
v.set_partial("item3", "I would use change", 14000, False)
spin()
pend = v._pending_row
check("pending live card created after a long pause", pend is not None and pend.tail == "I would use change")
check("pending card Send disabled (no finalized text yet)", not pend.ask_btn.isEnabled())
v.set_partial("item3", "I would use change data capture with AWS DMS", 14000, False)
spin()
check("pending card updated progressively (same widget)", v._pending_row is pend and "AWS DMS" in pend.tail)
v.grab().save(str(OUT / "after_2_pending_card.png"))
u2 = {"id": "u2", "start_offset_ms": 14000, "end_offset_ms": 19000, "is_final": True,
      "text": "I would use change data capture with AWS DMS and staging tables."}
v.add_utterance(u2)
spin()
check("pending card converted in place into the final card (no recreate)", v._paragraphs.get("u2") is pend)
check("converted card: Send enabled, no tail", pend.ask_btn.isEnabled() and pend.tail == "")
v.set_partial("item3", "", 14000, True)
spin()
check("two cards total, no duplicates", len(v._paragraphs) == 2 and v._pending_row is None)
v.grab().save(str(OUT / "after_3_finalized.png"))

# --- after a QUESTION card the server starts a new card, so live text must not continue it (no later jump)
v.add_utterance({"id": "uq", "start_offset_ms": 19100, "end_offset_ms": 19900, "is_final": True,
                 "text": "How would you tune Redshift?"})
spin()
v.set_partial("itemQ", "I would choose distribution keys", 20000, False)
spin()
check("live text after a question card opens a new card (mirrors server split)",
      v._pending_row is not None and v._paragraphs["uq"].tail == "")
v.add_utterance({"id": "ua", "start_offset_ms": 20000, "end_offset_ms": 23000, "is_final": True,
                 "text": "I would choose distribution keys based on joins."})
spin()
check("…and it is finalized in place in that card", v._paragraphs.get("ua") is not None and v._pending_row is None)
v.set_partial("itemQ", "", 20000, True)
spin()

# --- scrolled up: new live text must not force the view down
for i in range(12):
    v.add_utterance({"id": f"h{i}", "start_offset_ms": 20000 + i * 5000, "end_offset_ms": 22000 + i * 5000,
                     "is_final": True, "text": f"History paragraph {i} " + "words " * 25})
spin(10)
bar = v._scroll.verticalScrollBar()
bar.setValue(0)
spin()
v.set_partial("item9", "new live words while reading history", 90000, False)
spin(10)
check("scrolled-up reader is not pulled to the bottom", bar.value() == 0)
v.set_partial("item9", "", 90000, True)
spin()

# --- reconnect / history load: snapshot clears and re-renders with an in-flight partial, no duplicates
v.clear()
for u in (u1b, u2):
    v.add_utterance(u)
v.set_partial("itemR", "after reconnect partial", 19500, False)
spin()
check("after snapshot reload: 2 cards, partial inline once",
      len(v._paragraphs) == 2 and v._paragraphs["u2"].tail == "after reconnect partial" and v._pending_row is None)
v.grab().save(str(OUT / "after_4_reconnect.png"))

print(f"\n{sum(R)}/{len(R)} checks passed")
sys.exit(0 if all(R) else 1)

import asyncio
import sys
sys.path.append("C:/Users/RavishankarGavhane/Desktop/meeting-assistant/backend")
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models import TranscriptSegment, Meeting
from app.services.transcript_assembler import assemble_utterances
from app.services.meeting_service import snapshot

async def main():
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, "620f7320-f0f0-4879-8412-7ed37ec3cece")
        if not meeting:
            print("Meeting not found")
            return
        snap = await snapshot(db, meeting)
        print("Snapshot succeeded!")
        print("Meeting code:", snap.meeting.code)
        print("Raw segments count:", len(snap.segments))
        print("Assembled utterances count:", len(snap.utterances))
        for i, u in enumerate(snap.utterances):
            print(f"\n--- Utterance {i+1} [{u.start_offset_ms}ms - {u.end_offset_ms}ms] (question={u.is_question}) ---")
            print(f"\"{u.text}\"")
            if u.questions:
                print("  Detected question:", u.questions)

asyncio.run(main())

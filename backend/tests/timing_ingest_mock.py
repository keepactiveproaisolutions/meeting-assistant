"""Backend per-chunk overhead with speech-to-text MOCKED (no paid calls).

Measures what the server adds on top of the transcription API for one 2 s chunk, at different meeting lengths.
Uses throwaway meetings (ended afterwards). Run: .venv\\Scripts\\python.exe tests\\timing_ingest_mock.py
"""
import asyncio
import logging
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.core.dev_auth import ensure_dev_identity  # noqa: E402
from app.models import Meeting, TranscriptSegment  # noqa: E402
from app.services import meeting_service, openai_service  # noqa: E402

records: list[str] = []


class Grab(logging.Handler):
    def emit(self, r):
        records.append(r.getMessage())


logging.getLogger("timing.ingest").addHandler(Grab())
logging.getLogger("timing.ingest").setLevel(logging.INFO)


async def fake_stt(audio, filename="chunk.wav", context="", vocabulary=None):
    return "This is a mocked transcript chunk for timing."


openai_service.transcribe_wav = fake_stt


async def run(n_existing: int, chunks: int = 6):
    async with SessionLocal() as db:
        user, ws = await ensure_dev_identity(db)
        m = await meeting_service.create_meeting(db, ws.id, user.id, f"Timing mock {n_existing} (safe to ignore)")
        db.add_all(TranscriptSegment(meeting_id=m.id, chunk_index=i, start_offset_ms=i * 2000,
                                     end_offset_ms=i * 2000 + 2000, text=f"Earlier sentence number {i}.")
                   for i in range(n_existing))
        await db.commit()
        records.clear()
        for k in range(chunks):
            await meeting_service.ingest_audio_chunk(db, m, b"RIFF", n_existing + k, (n_existing + k) * 2000, 2000)
        meeting = await db.get(Meeting, m.id)
        meeting.status = "ended"
        await db.commit()
    totals = [int(re.search(r"total (\d+) ms", r).group(1)) for r in records]
    asm = [int(re.search(r"db\+assemble (\d+) ms", r).group(1)) for r in records]
    return statistics.median(totals), statistics.median(asm), records[-1]


async def main():
    for n in (0, 300, 1500):
        tot, asm, sample = await run(n)
        print(f"{n:5d} existing segments: median backend overhead {tot} ms (db+assemble {asm} ms)\n      e.g. {sample}")


asyncio.run(main())

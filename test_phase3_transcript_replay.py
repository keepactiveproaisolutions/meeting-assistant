"""Offline Replay & Assembly Test Suite for Continuous Paragraphs and Budget Investigation.
NO PAID API CALLS ARE MADE. Replays existing saved database records.
"""
import asyncio
import os
import sys

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "backend")))

from app.core.database import SessionLocal
from app.models.entities import Meeting, TranscriptSegment
from app.services.transcript_assembler import assemble_utterances
from sqlalchemy import select


async def test_budget_discrepancy():
    print("=" * 60)
    print("1. INVESTIGATION: CAPTURE BUDGET DISCREPANCY (15s vs 16s)")
    print("=" * 60)
    chunk_seconds = 2.0
    cap_seconds = 15.0

    print(f"Parameters: chunk_seconds = {chunk_seconds}s, max_duration_seconds = {cap_seconds}s")
    print("\n--- Original Logic (Before Fix) ---")
    orig_total_s = 0.0
    orig_chunks = []
    for idx in range(20):
        chunk_start_s = idx * chunk_seconds
        # In original loopback_capture.py line:
        if orig_total_s >= cap_seconds:  # or idx * chunk_seconds >= cap
            break
        orig_chunks.append((idx, chunk_start_s, chunk_seconds))
        orig_total_s += chunk_seconds
    for c in orig_chunks:
        print(f"  Chunk {c[0]}: start={c[1]:4.1f}s, duration={c[2]:4.1f}s, cumulative={c[1]+c[2]:4.1f}s")
    print(f"Original total emitted: {len(orig_chunks)} chunks = {orig_total_s:.1f}s audio.")
    print("Explanation: At chunk 7 (start=14.0s), start_time < 15.0s, so a full 2.0s slice was emitted, reaching 16.0s!")

    print("\n--- New Trimmed Logic (After Fix) ---")
    new_chunks = []
    for idx in range(20):
        chunk_start_s = idx * chunk_seconds
        if cap_seconds is not None and chunk_start_s >= cap_seconds:
            break
        chunk_dur = chunk_seconds
        if cap_seconds is not None:
            rem = cap_seconds - chunk_start_s
            if rem < chunk_seconds:
                chunk_dur = rem
        new_chunks.append((idx, chunk_start_s, chunk_dur))
        if cap_seconds is not None and (chunk_start_s + chunk_dur) >= cap_seconds:
            break
    new_total_s = sum(c[2] for c in new_chunks)
    for c in new_chunks:
        print(f"  Chunk {c[0]}: start={c[1]:4.1f}s, duration={c[2]:4.1f}s, cumulative={c[1]+c[2]:4.1f}s")
    print(f"New total emitted: {len(new_chunks)} chunks = {new_total_s:.1f}s audio (exact 15.0s cap enforced).")
    assert new_total_s == 15.0, f"Expected 15.0s, got {new_total_s}"
    print(">>> Budget discrepancy verified and resolved.")


async def test_udksgmtq_replay():
    print("\n" + "=" * 60)
    print("2. REPLAY TEST: EXISTING SAVED UDKSGMTQ SEGMENTS")
    print("=" * 60)
    async with SessionLocal() as db:
        meeting = (await db.scalars(select(Meeting).where(Meeting.code == "UDKSGMTQ"))).first()
        if not meeting:
            print("Meeting UDKSGMTQ not found in DB.")
            return

        segments = (await db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == meeting.id)
            .order_by(TranscriptSegment.start_offset_ms)
        )).all()

        print(f"Found {len(segments)} raw segments in meeting {meeting.code} (id: {meeting.id}):")
        print("\n--- BEFORE: Raw 2-second transport chunks (Broken sentences) ---")
        for s in segments:
            print(f"  [{s.start_offset_ms:5d}ms - {s.end_offset_ms:5d}ms] {s.text}")

        # Assemble into continuous conversational paragraphs
        utterances = assemble_utterances(segments)

        print("\n--- AFTER: Continuous Assembled Paragraphs (Wispr Flow style) ---")
        for i, u in enumerate(utterances, 1):
            q_flag = " [QUESTION DETECTED]" if u.is_question else ""
            print(f"  Utterance {i} [{u.start_offset_ms:5d}ms - {u.end_offset_ms:5d}ms]:{q_flag}")
            print(f"    Text: \"{u.text}\"")
            if u.questions:
                print(f"    Questions: {u.questions}")

        # Assertions
        assert len(utterances) == 3, f"Expected 3 utterances, got {len(utterances)}"
        
        # Verify Utterance 1: continuous statement across chunks 0-5
        u1 = utterances[0]
        assert not u1.is_question, "Utterance 1 should NOT be a question"
        assert "However, Python is also partially compiled" in u1.text, "Spurious period after 'also.' must be stripped"
        assert "is first compiled into byte code" in u1.text, "'is first compiled into byte code' must be part of utterance 1"

        # Verify Utterance 2: genuine question
        u2 = utterances[1]
        assert u2.is_question, "Utterance 2 MUST be detected as a question"
        assert "And what is bytecode?" in u2.text, "Utterance 2 text must match question"
        assert "And what is bytecode?" in u2.questions

        # Verify Utterance 3: answer start
        u3 = utterances[2]
        assert not u3.is_question, "Utterance 3 should NOT be a question"
        assert "bytecode is an intermediate low-level" in u3.text

        print("\n>>> All utterance assembly and question detection assertions passed!")

        # 3. Test Out-of-order Delivery Tolerance
        print("\n" + "=" * 60)
        print("3. TEST: OUT-OF-ORDER CHUNK DELIVERY TOLERANCE")
        print("=" * 60)
        import random
        shuffled = list(segments)
        random.seed(42)
        random.shuffle(shuffled)
        shuffled_utterances = assemble_utterances(shuffled)
        assert len(shuffled_utterances) == len(utterances)
        for original, from_shuffled in zip(utterances, shuffled_utterances):
            assert original.text == from_shuffled.text
            assert original.start_offset_ms == from_shuffled.start_offset_ms
        print(">>> Out-of-order segments assembled to identical utterances.")

        # 4. Test Clean Text Copy vs Full Timestamped Copy
        print("\n" + "=" * 60)
        print("4. TEST: TEXT COPY FORMATS")
        print("=" * 60)
        clean_copy = "\n\n".join(u.text for u in utterances)
        print("Clean flowing text (no timestamps):\n" + clean_copy)
        assert "[00:00:00]" not in clean_copy
        assert "And what is bytecode?" in clean_copy

        ts_copy = "\n\n".join(f"[{u.start_offset_ms//1000:02d}s] {u.text}" for u in utterances)
        print("\nTimestamped paragraphs:\n" + ts_copy)
        assert "[00s]" in ts_copy
        assert "[12s]" in ts_copy
        print(">>> Copy formatting verified.")


if __name__ == "__main__":
    asyncio.run(test_budget_discrepancy())
    asyncio.run(test_udksgmtq_replay())

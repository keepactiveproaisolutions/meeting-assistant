"""WebSocket event contract. Every message is an envelope:

    {"type": "<event>", "meeting_id": "<uuid>", "data": {...}, "ts": "<iso8601>"}
"""
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID


class Event(StrEnum):
    # server -> client
    SNAPSHOT = "session.snapshot"
    PRESENCE = "session.presence"
    MEETING_ENDED = "meeting.ended"
    MEETING_DOCUMENTS = "meeting.documents"
    DEMO_PREP_UPDATED = "demo_prep.updated"
    TRANSCRIPT_SEGMENT = "transcript.segment"
    TRANSCRIPT_UTTERANCE = "transcript.utterance"
    TRANSCRIPT_PARTIAL = "transcript.partial"  # Live mode provisional text, replaced by the final segment
    QUESTION_DETECTED = "question.detected"
    ANSWER_STARTED = "answer.started"
    ANSWER_SOURCES = "answer.sources"
    ANSWER_DELTA = "answer.delta"
    ANSWER_DIAGRAM = "answer.diagram"
    ANSWER_COMPLETED = "answer.completed"
    ANSWER_FAILED = "answer.failed"
    ERROR = "error"
    PONG = "pong"
    # client -> server
    PING = "ping"
    ASK = "ask"


def envelope(event: Event, meeting_id: UUID | str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": event.value,
        "meeting_id": str(meeting_id),
        "data": data or {},
        "ts": datetime.now(timezone.utc).isoformat(),
    }

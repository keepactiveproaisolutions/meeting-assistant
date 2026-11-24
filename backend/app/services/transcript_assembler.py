"""Utterance & Paragraph Assembler.

Assembles streaming audio chunk segments into continuous, readable conversational
paragraphs (inspired by Wispr Flow / Otter formatting) instead of fragmented 2-second lines.
Eliminates spurious mid-sentence breaks and false question detections.
"""
import re
from typing import Any

from app.schemas.schemas import QuestionOut, UtteranceOut
from app.services.question_detector import detect_questions

_STARTERS_PREFIX_RE = re.compile(
    r"^(?:and\s+|so\s+|but\s+|then\s+)?(?:what|why|how|when|where|who|whom|which|can|could|would|should|is|are|was|were|do|does|did|tell me|explain)\b",
    re.I,
)


def _get_val(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        val = item.get(key, default)
    else:
        val = getattr(item, key, default)
    return default if val is None else val


CHUNK_MS = 2000  # Standard mode chunk length


def _is_turn(seg: Any) -> bool:
    """Live mode turn (variable length, ends at a real pause) vs a fixed-length Standard chunk."""
    start = int(_get_val(seg, "start_offset_ms", 0))
    dur = int(_get_val(seg, "end_offset_ms", 0)) - start
    standard_chunk = start % CHUNK_MS == 0 and 0 < dur <= CHUNK_MS  # chunks start on 2 s boundaries, last <= 2 s
    return dur > 0 and not standard_chunk


def assemble_utterances(
    segments: list[Any],
    pause_threshold_ms: int = 1500,
    sentence_pause_ms: int = 1200,  # kept for API compatibility; punctuation-based splits are no longer used
    max_chars: int = 500,
    mid_sentence_pause_ms: int = 4500,
) -> list[UtteranceOut]:
    """Group raw chunk segments into utterances by REAL pauses.

    Chunks are transcribed independently, so a '?' or '.' at a chunk edge is not evidence of a sentence end.
    Continuous speech arrives as back-to-back chunks (gap ~0); a silent chunk is skipped by the capture client,
    which shows up as a gap of >= one chunk. Only that gap, or a very long utterance ending a sentence, starts a
    new card. Raw segments (with their own timestamps) are untouched and listed in segment_ids.
    """
    """Assembles raw segments into coherent paragraphs with context-aware question detection."""
    if not segments:
        return []

    # Sort stably by start offset then chunk index
    sorted_segs = sorted(
        segments,
        key=lambda s: (
            int(_get_val(s, "start_offset_ms", 0)),
            int(_get_val(s, "chunk_index", 0)),
        ),
    )

    utterances: list[UtteranceOut] = []
    current_id: str | None = None
    current_start: int = 0
    current_end: int = 0
    current_text: str = ""
    current_segment_ids: list[str] = []
    prev_seg: Any = None
    last_seg: Any = None

    for seg in sorted_segs:
        s_id = str(_get_val(seg, "id", ""))
        start_ms = int(_get_val(seg, "start_offset_ms", 0))
        end_ms = int(_get_val(seg, "end_offset_ms", 0))
        text = str(_get_val(seg, "text", "")).strip()

        if not text:
            continue
        prev_seg, last_seg = last_seg, seg

        if current_id is None:
            current_id = f"utt_{start_ms}"
            current_start = start_ms
            current_end = end_ms
            current_text = text
            current_segment_ids = [s_id] if s_id else []
            continue

        gap = start_ms - current_end
        prev_trimmed = current_text.rstrip()
        ends_terminal = prev_trimmed.endswith((".", "?", "!"))
        next_is_lower = text[0].islower() if text else False

        # Utterance boundaries: a real pause, or an overly long utterance at a sentence end.
        # A pause in the middle of a sentence (previous chunk ends without . ? ! — e.g. "for the" or a comma, or the
        # next chunk starts lowercase) keeps the same card if speech resumes within mid_sentence_pause_ms.
        mid_sentence = (not ends_terminal) or next_is_lower
        is_long_pause = gap >= (mid_sentence_pause_ms if mid_sentence else pause_threshold_ms)
        # long card: split only after a statement (never right after a question), unless next chunk continues it
        is_too_long = len(prev_trimmed) >= max_chars and prev_trimmed.endswith((".", "!")) and not next_is_lower

        # Live mode segments are whole spoken turns (ended by a real pause), so their punctuation is reliable —
        # unlike Standard 2 s chunks, where a "?" can sit mid-sentence. For turns, give each question its own card:
        # split after a question turn, so the next card never continues past a question (one question per card).
        prev_is_turn = _is_turn(prev_seg) if prev_seg is not None else False
        this_is_turn = _is_turn(seg)
        starts_upper = bool(text) and text[0].isupper()
        question_split = (
            prev_is_turn and this_is_turn and starts_upper and (
                prev_trimmed.endswith("?")))  # predictable: clients mirror it, so live text never jumps cards

        if is_long_pause or is_too_long or question_split:
            # Finalize previous utterance
            qs = detect_questions(current_text)
            utterances.append(
                UtteranceOut(
                    id=current_id,
                    start_offset_ms=current_start,
                    end_offset_ms=current_end,
                    text=current_text,
                    is_final=True,
                    is_question=bool(qs),
                    questions=[q.text for q in qs],
                    segment_ids=current_segment_ids,
                )
            )
            current_id = f"utt_{start_ms}"
            current_start = start_ms
            current_end = end_ms
            current_text = text
            current_segment_ids = [s_id] if s_id else []
        else:
            # Continue / append to current utterance
            # Strip spurious period from previous chunk boundary if the next chunk continues in lowercase
            if next_is_lower and prev_trimmed.endswith("."):
                prev_trimmed = prev_trimmed[:-1].rstrip()

            current_text = f"{prev_trimmed} {text}"
            current_end = max(current_end, end_ms)
            if s_id:
                current_segment_ids.append(s_id)

    if current_id is not None:
        qs = detect_questions(current_text)
        utterances.append(
            UtteranceOut(
                id=current_id,
                start_offset_ms=current_start,
                end_offset_ms=current_end,
                text=current_text,
                is_final=False,
                is_question=bool(qs),
                questions=[q.text for q in qs],
                segment_ids=current_segment_ids,
            )
        )

    return utterances

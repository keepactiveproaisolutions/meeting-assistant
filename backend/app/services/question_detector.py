"""Question detection on assembled transcript text.

Speech-to-text runs on independent ~2 s chunks, so punctuation at chunk edges is unreliable: one spoken question
often arrives as "What data quality framework? Did you implement, or was it? Implemented in the project." The
detector therefore merges consecutive question fragments into one question block instead of trusting every '?'.
"""
import re
from dataclasses import dataclass

_STARTERS = (
    "what", "why", "how", "when", "where", "who", "whom", "whose", "which", "can", "could",
    "would", "should", "shall", "do", "does", "did", "is", "are", "was", "were", "will",
    "have", "has", "had", "may", "might", "tell me", "walk me through", "explain", "describe",
    "any idea", "anyone know",
)
_LEAD_IN = r"(?:(?:and|so|but|also|then|or|next|now|okay|ok|alright|great|right|well)[,\s]+)*"
_STARTER_RE = re.compile(r"^" + _LEAD_IN + r"(?:" + "|".join(re.escape(s) for s in _STARTERS) + r")\b", re.I)
_REPLY_RE = re.compile(r"^(?:yes|no|yeah|yep|nope|okay|ok|sure|right|exactly|correct|got it|thanks?)\b", re.I)
_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]*")
_CONTINUATION_RE = re.compile(r"^(?:or|and|but|in|on|of|for|with|to|that|which|implemented|used|enforced|done|"
                              r"built|in the|at|from|by)\b", re.I)
MAX_BLOCK_SENTENCES = 12   # generous: a long spoken question must never be truncated
MAX_BLOCK_CHARS = 2000
SHORT_FRAGMENT_WORDS = 5   # a 2-second chunk usually carries <= ~5 words of a question


@dataclass
class Detected:
    text: str
    confidence: float


def _is_question_like(s: str) -> bool:
    return s.endswith("?") or bool(_STARTER_RE.match(s))


def _continues(s: str, last: str) -> bool:
    """Does fragment `s` belong to the question whose latest fragment is `last`?

    Chunk-edge punctuation splits one question into short pieces ("What data quality framework?" / "Did you
    implement, or was it?"). Two complete questions spoken back to back are both long, self-contained sentences
    and must stay separate. So join only when one side is clearly incomplete.
    """
    if _REPLY_RE.match(s):  # "Yes." / "Okay." after a question is the answer starting
        return False
    if s[:1].islower() or _CONTINUATION_RE.match(s):
        return True
    last_short = len(last.split()) <= SHORT_FRAGMENT_WORDS
    s_short = len(s.split()) <= SHORT_FRAGMENT_WORDS
    if last_short and (s.endswith("?") or _STARTER_RE.match(s) or s_short or not last.endswith("?")):
        return True  # previous piece was a chunk-sized fragment
    if not last.endswith("?") and s_short:
        return True  # unfinished question followed by a short tail
    return False


def detect_questions(text: str) -> list[Detected]:
    sentences = [s.strip() for s in _SENTENCE_RE.findall(text or "") if s.strip()]
    found: list[Detected] = []
    i = 0
    while i < len(sentences):
        s = sentences[i]
        if not _is_question_like(s):
            i += 1
            continue
        block = [s]
        j = i + 1
        while (j < len(sentences) and len(block) < MAX_BLOCK_SENTENCES
               and len(" ".join(block + [sentences[j]])) <= MAX_BLOCK_CHARS and _continues(sentences[j], block[-1])):
            block.append(sentences[j])
            j += 1
        # Drop trailing non-question fragments that are not followed by another question part
        while len(block) > 1 and not block[-1].endswith("?") and not _CONTINUATION_RE.match(block[-1]) \
                and len(block[-1].split()) > 7:
            block.pop()
            j -= 1
        q = " ".join(block)
        words = len(q.split())
        has_mark = "?" in q
        starts = bool(_STARTER_RE.match(block[0]))
        if words >= 3 and (has_mark or (starts and words >= 5)):
            conf = 0.98 if (has_mark and starts) else 0.85 if has_mark else 0.6
            if not q.endswith("?") and not has_mark:
                q = q.rstrip(".!") + "?"
            found.append(Detected(q, conf))
        i = max(j, i + 1)
    return found

"""Keywords for Live transcription (gpt-live-transcribe `keywords`).

The raw document vocabulary contains generic words, SQL keywords, months, people's names and user IDs. Measured on
2026-11-16: sending that unfiltered 60-term list made recognition WORSE ("Debezium" -> "Databricks"). Keep only
terms that look like product/technical identifiers, never personal names or IDs.
"""
from __future__ import annotations

import os
import re

_STOP = {
    # generic English / document words
    "bus", "service", "processing", "driver", "data", "check", "catalog", "count", "lake", "id", "job", "jobs",
    "number", "date", "location", "record", "records", "store", "item", "audit", "request", "platform", "topic",
    "raw", "integration", "project", "team", "client", "table", "tables", "file", "files", "report", "status",
    # environments / abbreviations that are ordinary speech
    "prod", "dev", "qa", "uat", "nb", "fc", "oct", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
    "sept", "nov", "dec", "utf", "ci", "cd", "null",
    # SQL keywords
    "select", "from", "where", "join", "group", "order", "insert", "update", "delete", "and", "or", "not", "as",
}
MAX_KEYWORDS = int(os.getenv("MA_LIVE_MAX_KEYWORDS", "30"))


def filter_keywords(terms: list[str], person_names: list[str] | None = None) -> list[str]:
    names = {p.lower() for n in (person_names or []) for p in re.split(r"\s+", n or "") if len(p) > 1}
    out: list[str] = []
    for t in terms:
        t = (t or "").strip()
        low = t.lower()
        if len(t) < 3 or low in _STOP or low in names:
            continue
        if t[0].islower():                      # usernames / ordinary words (e.g. "rdpadden1")
            continue
        if re.fullmatch(r"[A-Z][a-z]+", t) and len(t) < 5:  # short capitalised ordinary words ("Bus")
            continue
        if t not in out:
            out.append(t)
        if len(out) >= MAX_KEYWORDS:
            break
    return out

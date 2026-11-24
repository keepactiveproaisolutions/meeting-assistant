"""Text splitting for RAG ingestion."""
import re


def split_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> list[str]:
    """Split on paragraph/sentence boundaries into ~chunk_size char chunks with overlap."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    units: list[str] = []
    for para in re.split(r"\n{2,}", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= chunk_size:
            units.append(para)
        else:
            units.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", para) if s.strip())

    chunks: list[str] = []
    current = ""
    for unit in units:
        while len(unit) > chunk_size:  # hard-split very long sentences
            if current:
                chunks.append(current)
                current = ""
            chunks.append(unit[:chunk_size])
            unit = unit[chunk_size - overlap:]
        if current and len(current) + len(unit) + 1 > chunk_size:
            chunks.append(current)
            current = current[-overlap:] + " " + unit if overlap else unit
        else:
            current = f"{current}\n{unit}".strip() if current else unit
    if current:
        chunks.append(current)
    return chunks

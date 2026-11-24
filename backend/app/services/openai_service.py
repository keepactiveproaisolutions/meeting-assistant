"""OpenAI access. This is the ONLY module that talks to OpenAI; the key never leaves the backend."""
import asyncio
import io
from collections.abc import AsyncIterator
from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.services import mock_fixtures


@lru_cache
def client() -> AsyncOpenAI:
    # Never hang: 10 s to connect, 60 s of silence on a stream/read, and the SDK's automatic retries for transient errors.
    from openai import Timeout
    return AsyncOpenAI(api_key=get_settings().openai_api_key, timeout=Timeout(60.0, connect=10.0), max_retries=2)


def _chat_kwargs(messages: list[dict[str, str]], max_tokens: int | None = None) -> dict:
    """GPT-5-family models reject `temperature` and use `max_completion_tokens`; older models take both."""
    model = get_settings().openai_chat_model
    kw: dict = {"model": model, "messages": messages,
                # Routes requests that share a long identical prefix to the same cache (OpenAI prompt caching).
                "extra_body": {"prompt_cache_key": "meeting-assistant-live"}}
    if model.startswith(("gpt-5", "o1", "o3", "o4")):
        if max_tokens:
            kw["max_completion_tokens"] = max_tokens
        import os
        effort = (os.getenv("OPENAI_REASONING_EFFORT") or get_settings().openai_reasoning_effort or "").strip()
        if effort:  # live reading: low effort keeps time-to-first-word short on larger models
            kw["reasoning_effort"] = effort
    else:
        kw["temperature"] = 0.2
        if max_tokens:
            kw["max_tokens"] = max_tokens
    return kw


async def embed_texts(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    s = get_settings()
    if s.llm_mock:
        return [[0.0] * s.embedding_dimensions for _ in texts]
    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        resp = await client().embeddings.create(
            model=s.openai_embedding_model,
            input=texts[i : i + batch_size],
            dimensions=s.embedding_dimensions,
        )
        out.extend(d.embedding for d in resp.data)
    return out


async def transcribe_wav(audio: bytes, filename: str = "chunk.wav", context: str = "",
                         vocabulary: list[str] | None = None) -> str:
    """Transcribe one chunk. A vocabulary prompt (terms from the user's documents) measurably improves technical
    terms on 2 s chunks; only a short tail of the previous text is added, because long prior-transcript prompts
    caused repetition loops in testing."""
    s = get_settings()
    if s.llm_mock:
        return ""
    buf = io.BytesIO(audio)
    buf.name = filename
    kw: dict = {"model": s.openai_transcription_model, "file": buf, "response_format": "text"}
    if s.transcription_language.strip():
        kw["language"] = s.transcription_language.strip()
    prompt = ""
    if vocabulary:
        prompt = ("Technical meeting. Terms: " + ", ".join(vocabulary))[:600]
    if context.strip():
        prompt = f"{prompt} {context.strip()[-150:]}".strip()
    if prompt:
        kw["prompt"] = prompt
    resp = await client().audio.transcriptions.create(**kw)
    return (resp if isinstance(resp, str) else getattr(resp, "text", "")).strip()


async def stream_chat(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    if get_settings().llm_mock:
        user_msg = messages[-1]["content"] if messages else ""
        if isinstance(user_msg, list):  # screenshot message: use its text part
            user_msg = " ".join(p.get("text", "") for p in user_msg if p.get("type") == "text")
        full_text = mock_fixtures.pick(user_msg)
        # Stream in 18-char chunks to exercise partial Markdown & partial Mermaid handling
        step = 18
        for i in range(0, len(full_text), step):
            await asyncio.sleep(0.008)
            yield full_text[i : i + step]
        return

    # max_tokens is a hard cap on answer length (OpenAI/Anthropic latency guides: output length is the main latency lever).
    stream = await client().chat.completions.create(**_chat_kwargs(messages, max_tokens=2600), stream=True)
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


async def warm_prefix(messages: list[dict[str, str]]) -> None:
    """Send the shared prompt prefix once with a tiny output so the next real request hits the prompt cache."""
    if get_settings().llm_mock:
        return
    try:
        await client().chat.completions.create(**_chat_kwargs(messages, max_tokens=24))
    except Exception:  # noqa: BLE001 - the prompt is processed (and cached) even if the tiny output is rejected
        pass


async def complete_json(messages: list[dict[str, str]], max_tokens: int = 12000) -> str:
    """Single non-streamed completion constrained to a JSON object (used for Demo Prep)."""
    if get_settings().llm_mock:
        raise RuntimeError("Demo Prep generation needs the real model (LLM_MOCK is enabled).")
    resp = await client().chat.completions.create(**_chat_kwargs(messages, max_tokens),
                                                  response_format={"type": "json_object"})
    return resp.choices[0].message.content or "{}"

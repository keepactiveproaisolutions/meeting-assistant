"""PCM -> WAV helpers and resampling."""
import io
import wave

import numpy as np


def to_mono_16k(pcm: bytes, channels: int, src_rate: int, dst_rate: int) -> np.ndarray:
    """int16 interleaved PCM -> mono int16 at dst_rate (linear interpolation)."""
    data = np.frombuffer(pcm, dtype=np.int16)
    if channels > 1:
        data = data[: len(data) // channels * channels].reshape(-1, channels).mean(axis=1)
    data = data.astype(np.float32)
    if src_rate != dst_rate and len(data) > 1:
        n_out = int(len(data) * dst_rate / src_rate)
        data = np.interp(np.linspace(0, len(data) - 1, n_out), np.arange(len(data)), data)
    return np.clip(data, -32768, 32767).astype(np.int16)


def wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def is_silent(samples: np.ndarray, threshold: float = 40.0) -> bool:
    """RMS gate: skip uploading silent chunks (saves transcription cost)."""
    return samples.size == 0 or float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) < threshold

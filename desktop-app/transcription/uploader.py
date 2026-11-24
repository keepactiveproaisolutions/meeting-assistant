"""Background uploader: sends 2 s audio chunks to the backend for transcription."""
import logging
import queue
import threading
import time

import httpx

from audio.loopback_capture import AudioChunk
from config.settings import settings

log = logging.getLogger(__name__)


class ChunkUploader:
    def __init__(self, token: str | None, meeting_id: str, on_error=None, max_queue: int = 150) -> None:
        # 150 x ~64 KB chunks = ~5 min of speech (~10 MB) can wait while the network/backend recovers.
        self._q: queue.Queue[AudioChunk | None] = queue.Queue(maxsize=max_queue)
        self._url = f"{settings.api_url}/api/v1/meetings/{meeting_id}/audio"
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._on_error = on_error or (lambda m: log.error(m))
        self._thread = threading.Thread(target=self._run, name="chunk-uploader", daemon=True)
        self.uploaded = 0
        self.failed = 0
        self.dropped = 0
        self._enq: dict[int, float] = {}  # chunk index -> enqueue time (timing log only)

    def start(self) -> None:
        self._thread.start()

    def stop(self, drain: bool = True) -> None:
        """Stops uploader. If drain=True, finishes processing in-flight captured audio before exiting."""
        if not drain:
            while not self._q.empty():
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break
        self._q.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout=15)

    def submit(self, chunk: AudioChunk) -> None:
        try:
            self._q.put_nowait(chunk)
        except queue.Full:  # backend unreachable for minutes: drop oldest to keep memory bounded
            try:
                old = self._q.get_nowait()
                if old is None:  # never swallow the stop signal
                    self._q.put_nowait(None)
                    return
                self._enq.pop(old.index, None)
                self.dropped += 1
                self._on_error(f"Upload backlog full — dropped {self.dropped} oldest audio chunk(s). Check the backend.")
            except queue.Empty:
                pass
            self._q.put_nowait(chunk)
        self._enq[chunk.index] = time.perf_counter()

    def _run(self) -> None:
        with httpx.Client(timeout=30) as client:
            while (chunk := self._q.get()) is not None:
                # Retry only when the request never reached the backend (nothing was transcribed or billed).
                t_deq = time.perf_counter()
                wait_ms = (t_deq - self._enq.pop(chunk.index, t_deq)) * 1000
                for attempt in range(4):
                    try:
                        r = client.post(
                            self._url, headers=self._headers,
                            files={"file": ("chunk.wav", chunk.wav, "audio/wav")},
                            data={"chunk_index": chunk.index, "offset_ms": chunk.offset_ms,
                                  "duration_ms": chunk.duration_ms})
                        r.raise_for_status()
                        self.uploaded += 1
                        logging.getLogger("timing.upload").info(
                            "chunk %s: queue wait %d ms | request %d ms | backlog %d",
                            chunk.index, wait_ms, (time.perf_counter() - t_deq) * 1000, self._q.qsize())
                        break
                    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                        if attempt == 3:
                            self.failed += 1
                            self._on_error(f"Backend unreachable; chunk {chunk.index} not saved: {exc}")
                        else:
                            time.sleep(1.5 * (attempt + 1))
                    except httpx.HTTPError as exc:
                        self.failed += 1
                        self._on_error(f"Upload failed for chunk {chunk.index}: {exc}")
                        break

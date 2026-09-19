import queue
import re
import threading
import time
from dataclasses import dataclass, field

import langid.langid as langid
import miniaudio
import numpy as np
import sounddevice as sd

from . import indicator
from .config import config
from .error_log import debug_logger

# The module-level langid.classify()/set_languages() only expose an unnormalized,
# unbounded score (not a probability), so a real 0-1 confidence needs the underlying
# identifier constructed with norm_probs=True instead.
_lang_identifier = langid.LanguageIdentifier.from_modelstring(langid.model, norm_probs=True)
_lang_identifier.set_languages(["en", "es"])

_MARKDOWN_EMPHASIS = re.compile(r"[*_]{1,3}")
_WHITESPACE = re.compile(r"\s{2,}")


def _strip_markdown(text: str) -> str:
    return _WHITESPACE.sub(" ", _MARKDOWN_EMPHASIS.sub(" ", text)).strip()

interrupt_event = threading.Event()

_utterance_voice: str | None = None


def begin_utterance() -> None:
    """Reset the locked edge-tts voice; call once per new reply so language detection re-runs."""
    global _utterance_voice
    _utterance_voice = None


def _resolve_edge_voice(text: str) -> str:
    global _utterance_voice
    if _utterance_voice is None:
        lang, confidence = _lang_identifier.classify(text)
        is_spanish = lang == "es" and confidence >= config.spanish_confidence_threshold
        _utterance_voice = config.edge_voice_es if is_spanish else config.edge_voice
    return _utterance_voice

_FRAME_SAMPLES = 1024
_LEVEL_SCALE = 6.0
_N_BANDS = 5


def _levels_from_pcm16(raw: bytes) -> list[float]:
    if not raw:
        return [0.0] * _N_BANDS
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size < 2:
        return [0.0] * _N_BANDS
    spectrum = np.abs(np.fft.rfft(samples))
    bands = np.array_split(spectrum, _N_BANDS)
    return [min(1.0, float(np.sqrt(np.mean(b**2))) * _LEVEL_SCALE) if b.size else 0.0 for b in bands]


def _write_with_levels(stream, raw: bytes) -> bool:
    frame_bytes = _FRAME_SAMPLES * 2
    for offset in range(0, len(raw), frame_bytes):
        if interrupt_event.is_set():
            return False
        frame = raw[offset : offset + frame_bytes]
        indicator.set_levels(_levels_from_pcm16(frame))
        stream.write(frame)
    return True


@dataclass
class PreparedSpeech:
    """Audio for one sentence, synthesized in a background thread as soon as the text is known."""

    chunks: "queue.Queue[bytes | None]"
    sample_rate: int
    channels: int = 1
    text: str = ""  # diagnostics only, see debug_logger calls below
    created_at: float = field(default_factory=time.monotonic)


class _QueueSource(miniaudio.StreamableSource):
    """Feeds mp3 bytes to the miniaudio decoder as they arrive from the network."""

    def __init__(self) -> None:
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._buffer = bytearray()
        self._eof = False

    def push(self, data: bytes) -> None:
        self._queue.put(data)

    def end(self) -> None:
        self._queue.put(None)

    def read(self, num_bytes: int) -> bytes:
        while not self._eof and len(self._buffer) < num_bytes:
            chunk = self._queue.get()
            if chunk is None:
                self._eof = True
                break
            self._buffer.extend(chunk)
        result = bytes(self._buffer[:num_bytes])
        del self._buffer[:num_bytes]
        return result


_EDGE_SAMPLE_RATE = 24000


def _prepare_edge(text: str) -> PreparedSpeech:
    import edge_tts

    communicate = edge_tts.Communicate(text, _resolve_edge_voice(text), rate=config.edge_rate)
    source = _QueueSource()
    chunks: "queue.Queue[bytes | None]" = queue.Queue()

    def _fetch() -> None:
        try:
            for chunk in communicate.stream_sync():
                if interrupt_event.is_set():
                    break
                if chunk["type"] == "audio":
                    source.push(chunk["data"])
        finally:
            source.end()

    def _decode() -> None:
        try:
            pcm_stream = miniaudio.stream_any(
                source,
                source_format=miniaudio.FileFormat.MP3,
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=1,
                sample_rate=_EDGE_SAMPLE_RATE,
            )
            for frames in pcm_stream:
                if interrupt_event.is_set():
                    break
                if frames:
                    chunks.put(frames.tobytes())
        except miniaudio.DecodeError:
            if not interrupt_event.is_set():
                raise
        finally:
            chunks.put(None)

    threading.Thread(target=_fetch, daemon=True).start()
    threading.Thread(target=_decode, daemon=True).start()
    debug_logger.debug("PREPARE text=%r", text)
    return PreparedSpeech(chunks, _EDGE_SAMPLE_RATE, text=text)


def prepare(text: str) -> PreparedSpeech:
    """Kick off synthesis for one sentence in the background. Call as soon as the sentence's
    text is known, well before it's due to play, so the network/synthesis latency overlaps
    with whatever is currently playing instead of stalling playback."""
    return _prepare_edge(_strip_markdown(text))


def play(prepared: PreparedSpeech) -> None:
    """Play back audio from prepare(), blocking until it finishes."""
    indicator.start_speaking()
    stream = None
    play_started = time.monotonic()
    first_chunk_logged = False
    try:
        while True:
            if interrupt_event.is_set():
                break
            try:
                chunk = prepared.chunks.get(timeout=0.1)
            except queue.Empty:
                continue
            if not first_chunk_logged:
                debug_logger.debug(
                    "PLAY text=%r lead=%.2fs wait_for_first_chunk=%.2fs",
                    prepared.text, play_started - prepared.created_at, time.monotonic() - play_started,
                )
                first_chunk_logged = True
            if chunk is None:
                break
            if stream is None:
                stream = sd.RawOutputStream(
                    samplerate=prepared.sample_rate, channels=prepared.channels, dtype="int16"
                )
                stream.start()
            if not _write_with_levels(stream, chunk):
                break
    finally:
        if stream is not None:
            stream.stop()
            stream.close()
        indicator.stop_speaking()

import pathlib
import queue
import re
import threading
import time
import tomllib
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
#
# Building this from the packed model string takes the better part of a second, so it
# runs in a background thread kicked off at import time instead of blocking the import
# itself -- callers that need it (_resolve_edge_voice, wait_until_ready) block on
# _lang_identifier_ready, which lets this overlap with stt's model load instead of
# running after it.
_lang_identifier: langid.langid.LanguageIdentifier | None = None
_lang_identifier_ready = threading.Event()


def _load_lang_identifier() -> None:
    global _lang_identifier
    identifier = langid.LanguageIdentifier.from_modelstring(langid.model, norm_probs=True)
    identifier.set_languages(["en", "es"])
    _lang_identifier = identifier
    _lang_identifier_ready.set()


threading.Thread(target=_load_lang_identifier, daemon=True).start()


def wait_until_ready() -> None:
    """Blocks until the language-identification model has finished loading."""
    _lang_identifier_ready.wait()

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_MARKDOWN_EMPHASIS = re.compile(r"[*_]{1,3}")
# Horizontal rules (--- / *** / ___) that arrive as isolated "sentences" after the
# newline splitter in assistant.py -- no spoken equivalent, just strip them entirely.
_HORIZONTAL_RULE = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)
_WHITESPACE = re.compile(r"\s{2,}")

_PRONUNCIATIONS_FILE = pathlib.Path(__file__).parent.parent / "pronunciations.toml"
_pronunciations: dict[str, tuple[re.Pattern, str]] = {}


def _load_pronunciations() -> None:
    global _pronunciations
    if not _PRONUNCIATIONS_FILE.exists():
        _pronunciations = {}
        return
    with open(_PRONUNCIATIONS_FILE, "rb") as f:
        data = tomllib.load(f)
    entries = data.get("pronunciations", {})
    _pronunciations = {
        word: (re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)", re.IGNORECASE), spoken)
        for word, spoken in entries.items()
    }


_load_pronunciations()


def _apply_pronunciations(text: str) -> str:
    for pattern, spoken in _pronunciations.values():
        text = pattern.sub(spoken, text)
    return text


def _strip_markdown(text: str) -> str:
    # Sentence splitting in assistant.py treats a bare newline as a boundary, so a fenced
    # code block's opening/closing ``` line routinely arrives here as its own isolated
    # "sentence" (the paired regex above only matches when both fences land in the same
    # call, e.g. a one-line block) -- stripped down to nothing rather than spoken as literal
    # backticks. Confirmed live 2026-09-19: a reply containing a code block froze the whole
    # app, tracked to this text reaching edge-tts unsanitized; see prepare()'s empty-text
    # guard below for the other half of that fix.
    text = _CODE_FENCE.sub(" ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = text.replace("`", " ")
    text = _HORIZONTAL_RULE.sub(" ", text)
    return _WHITESPACE.sub(" ", _MARKDOWN_EMPHASIS.sub(" ", text)).strip()

interrupt_event = threading.Event()

_utterance_voice: str | None = None


def begin_utterance() -> None:
    """Reset the locked edge-tts voice; call once per new reply so language detection re-runs.
    Marks a reply in progress so the Bluetooth keepalive thread hands the output stream off to
    playback (and never closes it between sentences) until end_utterance()."""
    global _utterance_voice
    _utterance_voice = None
    _reply_active.set()


def _resolve_edge_voice(text: str) -> str:
    global _utterance_voice
    if _utterance_voice is None:
        if _lang_identifier is None:
            _lang_identifier_ready.wait()
        lang, confidence = _lang_identifier.classify(text)
        is_spanish = lang == "es" and confidence >= config.spanish_confidence_threshold
        _utterance_voice = config.edge_voice_es if is_spanish else config.edge_voice
    return _utterance_voice

_FRAME_SAMPLES = 1024
_LEVEL_SCALE = 6.0
_N_BANDS = 5
_SILENCE_CHUNK = bytes(_FRAME_SAMPLES * 2)

_write_lock = threading.Lock()
_is_speaking = threading.Event()
# Set for the whole span of a reply (begin_utterance -> end_utterance), across all its sentences.
# The keepalive thread hands the output stream off to playback while this is set, so it never
# closes the shared stream between sentences.
_reply_active = threading.Event()
_last_speech_end = 0.0

_WRITE_OK = "ok"
_WRITE_INTERRUPT = "interrupt"
_WRITE_ERROR = "error"
# How many times play() will re-point at the current default device and retry when the output
# write fails mid-reply (e.g. Bluetooth earbuds drop) before giving up on the current sentence.
_MAX_RECOVERY_ATTEMPTS = 3
# If the output buffer stops draining for this long, the device has stalled (almost always a
# Bluetooth link half-dropping mid-reply). A plain blocking write() would hang for however long
# the link takes to come back -- observed ~195s live, which froze the reply and stranded the
# "Speaking" indicator. _write_with_levels bails out as a device error at this threshold instead.
_WRITE_STALL_TIMEOUT = 5.0


def _levels_from_pcm16(raw: bytes) -> list[float]:
    if not raw:
        return [0.0] * _N_BANDS
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size < 2:
        return [0.0] * _N_BANDS
    spectrum = np.abs(np.fft.rfft(samples))
    bands = np.array_split(spectrum, _N_BANDS)
    return [min(1.0, float(np.sqrt(np.mean(b**2))) * _LEVEL_SCALE) if b.size else 0.0 for b in bands]


def _write_with_levels(stream, raw: bytes) -> str:
    """Write one sentence's PCM frame by frame, driving the level meter. Returns _WRITE_OK,
    _WRITE_INTERRUPT (user cut in), or _WRITE_ERROR (the output device stalled or dropped -- the
    stream is closed here so the caller can re-point at a working device).

    Never blocks longer than _WRITE_STALL_TIMEOUT: instead of handing a whole frame to a blocking
    write() (which hangs indefinitely when a Bluetooth link stalls mid-reply), it waits for buffer
    space in short polls and writes only as much as fits, so a stalled device surfaces as an error
    the caller can recover from rather than freezing the reply."""
    frame_bytes = _FRAME_SAMPLES * 2
    buf = memoryview(raw)
    for offset in range(0, len(raw), frame_bytes):
        if interrupt_event.is_set():
            return _WRITE_INTERRUPT
        frame = buf[offset : offset + frame_bytes]
        indicator.set_levels(_levels_from_pcm16(frame))
        pos = 0
        stall_deadline = time.monotonic() + _WRITE_STALL_TIMEOUT
        try:
            with _write_lock:
                while pos < len(frame):
                    if interrupt_event.is_set():
                        return _WRITE_INTERRUPT
                    available = stream.write_available
                    if available <= 0:
                        # Buffer full and not draining -- the device has backed up. Wait briefly
                        # and give up only if nothing frees up within the stall window; a healthy
                        # device frees space every iteration, resetting the deadline below.
                        if time.monotonic() > stall_deadline:
                            raise TimeoutError("output device stalled")
                        time.sleep(0.01)
                        continue
                    stall_deadline = time.monotonic() + _WRITE_STALL_TIMEOUT
                    n = min(available, (len(frame) - pos) // 2)
                    if n <= 0:
                        break
                    stream.write(bytes(frame[pos : pos + n * 2]))
                    pos += n * 2
        except Exception:
            _close_stream(drain=False)
            return _WRITE_ERROR
    return _WRITE_OK


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


def _empty_speech(text: str) -> PreparedSpeech:
    chunks: "queue.Queue[bytes | None]" = queue.Queue()
    chunks.put(None)
    return PreparedSpeech(chunks, _EDGE_SAMPLE_RATE, text=text)


def prepare(text: str) -> PreparedSpeech:
    """Kick off synthesis for one sentence in the background. Call as soon as the sentence's
    text is known, well before it's due to play, so the network/synthesis latency overlaps
    with whatever is currently playing instead of stalling playback."""
    stripped = _strip_markdown(text)
    # A "sentence" that's pure markdown noise (e.g. a lone code-fence line, see
    # _strip_markdown's comment) strips down to nothing -- skip the network call entirely
    # rather than asking edge-tts to synthesize empty text.
    if not stripped:
        return _empty_speech(text)
    return _prepare_edge(_apply_pronunciations(stripped))


# The output stream is kept open across every sentence in a reply and only torn down at the
# end (end_utterance) or on interrupt. Opening a stream is cheap (~6ms) but closing it costs
# ~215ms (measured) -- doing that per sentence was the entire audible gap between sentences.
_stream_lock = threading.Lock()
_stream: sd.RawOutputStream | None = None
_stream_format: tuple[int, int] | None = None


def _get_stream(sample_rate: int, channels: int) -> sd.RawOutputStream:
    global _stream, _stream_format
    with _stream_lock:
        if _stream is not None and _stream_format != (sample_rate, channels):
            _stream.stop()
            _stream.close()
            _stream = None
        if _stream is None:
            _stream = sd.RawOutputStream(samplerate=sample_rate, channels=channels, dtype="int16")
            _stream.start()
            _stream_format = (sample_rate, channels)
        return _stream


def _close_stream(drain: bool = True) -> None:
    """Tear down the shared output stream. drain=True (a clean end of reply) lets buffered audio
    finish so the tail isn't clipped; drain=False (an interrupt, or a stalled/dead device) aborts
    immediately -- draining a device that isn't actually playing would hang. stop()/abort()/close()
    can raise if the device vanished, so that's swallowed and the reference dropped regardless, so a
    dead device is never left cached as the live stream and play()'s finally can't be derailed
    before it resets the "Speaking" indicator."""
    global _stream, _stream_format
    with _write_lock:
        with _stream_lock:
            if _stream is not None:
                try:
                    if drain:
                        _stream.stop()
                    else:
                        _stream.abort()
                    _stream.close()
                except Exception:
                    pass
                _stream = None
                _stream_format = None


def _reset_audio() -> None:
    """Re-enumerate PortAudio's devices so a disconnected output falls back to the current Windows
    default. PortAudio caches the device list at init, so without this a dropped BT device stays
    the (dead) default and every reopen fails. Only called during playback, when the mic is closed
    (the turn loop is sequential) -- terminating PortAudio with an input stream open would break
    recording, but that never overlaps play()."""
    global _stream, _stream_format
    with _write_lock:
        with _stream_lock:
            if _stream is not None:
                try:
                    _stream.abort()
                    _stream.close()
                except Exception:
                    pass
                _stream = None
                _stream_format = None
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass


def _output_device_name() -> str:
    try:
        return str(sd.query_devices(kind="output")["name"])
    except Exception:
        return ""


def _keepalive_wanted() -> bool:
    """True only while the configured Bluetooth device is the active output *and* we're still
    inside the short warm-up window after the last reply. An unset TTS_KEEPALIVE_DEVICE disables
    keepalive entirely -- continuous silence streaming was destabilizing the BT link (dropouts),
    so it now runs only briefly and only on the one device that needs it."""
    target = config.tts_keepalive_device.strip().lower()
    if not target:
        return False
    if time.monotonic() - _last_speech_end > config.tts_keepalive_seconds:
        return False
    return target in _output_device_name().lower()


def end_utterance() -> None:
    """Called once when a whole reply has finished playing. Starts the Bluetooth warm-up window so
    back-to-back turns don't clip on a slept link -- but only when the configured BT device is the
    active output; otherwise the device is released immediately so it can idle normally."""
    global _last_speech_end
    _last_speech_end = time.monotonic()
    _reply_active.clear()
    if not _keepalive_wanted():
        _close_stream()


def _keepalive_worker() -> None:
    """Keeps the Bluetooth link warm for a few seconds after a reply so the earbuds don't drop into
    power-save and clip the start of the next one -- then releases it so they can idle. Runs only
    for the configured device and only within that window; idle and cheap otherwise. Hands off
    entirely (never touches the shared stream) while a reply is playing."""
    while True:
        if _reply_active.is_set() or _is_speaking.is_set():
            time.sleep(0.05)
            continue
        if _keepalive_wanted():
            stream_error = False
            with _write_lock:
                if _reply_active.is_set() or _is_speaking.is_set():
                    continue
                try:
                    stream = _get_stream(_EDGE_SAMPLE_RATE, 1)
                    stream.write(_SILENCE_CHUNK)
                except Exception:
                    stream_error = True
            if stream_error:
                _close_stream(drain=False)
                time.sleep(0.1)
        else:
            # Warm window elapsed, or another output is active: release any stream a keepalive
            # reply left open so the device stops being held busy. Only silence is buffered, so
            # abort rather than drain (and never risk draining a device that has since stalled).
            if _stream is not None:
                _close_stream(drain=False)
            time.sleep(0.2)


threading.Thread(target=_keepalive_worker, daemon=True).start()


def play(prepared: PreparedSpeech) -> None:
    """Play back audio from prepare(), blocking until it finishes. The output stream is shared
    across the reply's sentences and left open here; the caller ends the reply with
    end_utterance(). An interrupt closes it immediately so playback is cut on the spot. If the
    output device drops mid-reply (e.g. Bluetooth earbuds disconnect) the audio is re-pointed at
    the current default device and playback continues there, so the rest of the reply still plays
    instead of the whole reply dying."""
    _is_speaking.set()
    indicator.start_speaking()
    stream = None
    play_started = time.monotonic()
    first_chunk_logged = False
    interrupted = False
    recovery_attempts = 0
    try:
        while True:
            if interrupt_event.is_set():
                interrupted = True
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
                try:
                    stream = _get_stream(prepared.sample_rate, prepared.channels)
                except Exception:
                    # No usable output device (e.g. the earbuds just dropped and the default
                    # hasn't settled). Re-point at the current default and retry, bounded so a
                    # truly dead output can't spin forever.
                    if recovery_attempts >= _MAX_RECOVERY_ATTEMPTS:
                        break
                    recovery_attempts += 1
                    _reset_audio()
                    time.sleep(0.1)
                    continue
            result = _write_with_levels(stream, chunk)
            if result == _WRITE_INTERRUPT:
                interrupted = True
                break
            if result == _WRITE_ERROR:
                # Device dropped mid-write. Re-point at the new default and keep playing the rest
                # of the reply there. The interrupted chunk's tail is lost, but the reply survives.
                stream = None
                if recovery_attempts >= _MAX_RECOVERY_ATTEMPTS:
                    break
                recovery_attempts += 1
                _reset_audio()
    finally:
        _is_speaking.clear()
        if interrupted:
            _close_stream(drain=False)
        indicator.stop_speaking()

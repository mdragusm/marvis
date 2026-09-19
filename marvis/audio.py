import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000

# Set directly by assistant.py's suppressing key-hook callback on every down/up edge of
# the push-to-talk key, rather than polled independently (e.g. via GetAsyncKeyState).
# A suppressing low-level keyboard hook prevents Windows from ever updating the
# GetAsyncKeyState table for that key -- confirmed on this machine: a separate process
# polling GetAsyncKeyState saw zero edges while the hook was suppressing the key, even
# though it was being held down. Driving detection off the hook's own callback instead
# means detection can never disagree with what's actually being suppressed.
ptt_down = threading.Event()


def record_while_key_held() -> np.ndarray:
    frames: list[np.ndarray] = []

    def callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback):
        while ptt_down.is_set():
            sd.sleep(50)

    if not frames:
        return np.zeros((0,), dtype="float32")
    return np.concatenate(frames, axis=0).flatten()


def record_until_silence(
    max_seconds: float = 10.0,
    silence_threshold: float = 0.02,
    silence_duration: float = 1.0,
) -> np.ndarray:
    chunk_duration = 0.05
    chunks_per_second = int(1 / chunk_duration)
    max_chunks = int(max_seconds * chunks_per_second)
    silence_chunks_needed = int(silence_duration * chunks_per_second)

    frames: list[np.ndarray] = []
    silent_chunks = 0

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
        for _ in range(max_chunks):
            chunk, _ = stream.read(int(SAMPLE_RATE * chunk_duration))
            frames.append(chunk.copy())
            if np.abs(chunk).mean() < silence_threshold:
                silent_chunks += 1
                if silent_chunks >= silence_chunks_needed:
                    break
            else:
                silent_chunks = 0

    return np.concatenate(frames, axis=0).flatten()

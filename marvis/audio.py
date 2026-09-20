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
    max_seconds: float = 20.0,
    silence_threshold: float = 0.02,
    silence_duration: float = 2.0,
    speech_start_timeout: float = 3.0,
) -> np.ndarray:
    chunk_duration = 0.05
    chunks_per_second = int(1 / chunk_duration)
    max_chunks = int(max_seconds * chunks_per_second)
    silence_chunks_needed = int(silence_duration * chunks_per_second)
    speech_start_chunks_allowed = int(speech_start_timeout * chunks_per_second)

    frames: list[np.ndarray] = []
    silent_chunks = 0
    speech_started = False

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
        for _ in range(max_chunks):
            chunk, _ = stream.read(int(SAMPLE_RATE * chunk_duration))
            frames.append(chunk.copy())
            if np.abs(chunk).mean() < silence_threshold:
                silent_chunks += 1
                # Before real speech starts, a longer timeout applies -- there's usually a
                # beat between the wake word registering and the user actually starting to
                # talk (composing their thought, recovering from having to repeat the wake
                # word), and the tighter post-speech silence_duration would otherwise end
                # the recording during that gap, before any of the real command is captured.
                needed = silence_chunks_needed if speech_started else speech_start_chunks_allowed
                if silent_chunks >= needed:
                    break
            else:
                speech_started = True
                silent_chunks = 0

    return np.concatenate(frames, axis=0).flatten()

from collections import deque

import numpy as np
import sounddevice as sd

from .config import config

SAMPLE_RATE = 16000
CHUNK = 1280  # openWakeWord expects ~80ms frames at 16kHz

# How many recent frames' amplitude count as "was it loud enough recently" for the
# noise-floor gate below -- roughly 800ms at 80ms/frame.
_RECENT_AMPLITUDE_WINDOW = 10

# Importing openwakeword.model drags in scikit-learn/scipy (~1s) just to construct the
# model, which is wasted work for anyone in push-to-talk or text mode -- the default and,
# likely, common case. Deferred to first actual use in wait_for_wake_word instead.
_model = None


def _get_model():
    global _model
    if _model is None:
        from openwakeword.model import Model

        _model = Model(wakeword_models=[config.wake_word], inference_framework="onnx")
    return _model


def wait_for_wake_word(
    threshold: float = config.wake_threshold,
    noise_floor: float = config.wake_noise_floor,
    mode_check=None,
) -> bool:
    """Block until the wake word is heard. Returns False early if mode_check() turns falsy."""
    # openWakeWord's confidence score peaks a frame or two after the loudest part of the
    # phrase (its own internal buffering), so requiring both the noise-floor and confidence
    # checks to pass on the exact same frame missed real, high-confidence detections --
    # confirmed live: a frame logged confidence=0.999 while amplitude on that same frame
    # was below noise_floor, rejecting an otherwise clean detection. Checking confidence
    # against the recent amplitude window instead of the single current frame fixes that
    # without weakening the noise-floor gate's actual purpose (rejecting the model firing on
    # near-silent ambient noise, e.g. PC fan hum).
    model = _get_model()
    recent_amplitudes: "deque[float]" = deque(maxlen=_RECENT_AMPLITUDE_WINDOW)
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK) as stream:
        while True:
            if mode_check is not None and not mode_check():
                return False
            chunk, _ = stream.read(CHUNK)
            flat = chunk.flatten()
            # Always feed the model so its internal streaming buffer stays warm.
            prediction = model.predict(flat)
            recent_amplitudes.append(float(np.abs(flat).mean()))
            if max(recent_amplitudes) >= noise_floor and prediction[config.wake_word] > threshold:
                return True

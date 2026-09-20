import threading

import numpy as np
from faster_whisper import WhisperModel

# Loading the "small" model takes the better part of a second, so it runs in a
# background thread kicked off at import time instead of blocking the import itself --
# this lets it overlap with tts's language-model load instead of running after it.
_model: WhisperModel | None = None
_model_ready = threading.Event()


def _load_model() -> None:
    global _model
    _model = WhisperModel("small", device="cpu", compute_type="int8")
    _model_ready.set()


threading.Thread(target=_load_model, daemon=True).start()


def wait_until_ready() -> None:
    """Blocks until the speech-to-text model has finished loading."""
    _model_ready.wait()


def transcribe(audio: np.ndarray) -> str:
    _model_ready.wait()
    segments, _ = _model.transcribe(audio)
    return " ".join(segment.text.strip() for segment in segments)

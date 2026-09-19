import numpy as np
from faster_whisper import WhisperModel

_model = WhisperModel("small", device="cpu", compute_type="int8")


def transcribe(audio: np.ndarray) -> str:
    segments, _ = _model.transcribe(audio)
    return " ".join(segment.text.strip() for segment in segments)

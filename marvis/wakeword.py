import numpy as np
import sounddevice as sd
from openwakeword.model import Model

from .config import config

SAMPLE_RATE = 16000
CHUNK = 1280  # openWakeWord expects ~80ms frames at 16kHz

_model = Model(wakeword_models=[config.wake_word], inference_framework="onnx")


def wait_for_wake_word(
    threshold: float = config.wake_threshold,
    noise_floor: float = config.wake_noise_floor,
    mode_check=None,
) -> bool:
    """Block until the wake word is heard. Returns False early if mode_check() turns falsy."""
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK) as stream:
        while True:
            if mode_check is not None and not mode_check():
                return False
            chunk, _ = stream.read(CHUNK)
            flat = chunk.flatten()
            # Always feed the model so its internal streaming buffer stays warm, but only
            # trust a detection when the frame is louder than ambient noise (e.g. PC fan hum).
            prediction = _model.predict(flat)
            if np.abs(flat).mean() >= noise_floor and prediction[config.wake_word] > threshold:
                return True

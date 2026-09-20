import os
from dataclasses import dataclass
from enum import Enum

from dotenv import load_dotenv

load_dotenv()


class Mode(Enum):
    PUSH_TO_TALK = "push_to_talk"
    WAKE_WORD = "wake_word"
    TEXT = "text"


@dataclass
class Config:
    edge_voice: str = os.getenv("EDGE_VOICE", "en-US-GuyNeural")
    edge_voice_es: str = os.getenv("EDGE_VOICE_ES", "es-GT-AndresNeural")
    edge_rate: str = os.getenv("EDGE_RATE", "+0%")
    spanish_confidence_threshold: float = float(os.getenv("SPANISH_CONFIDENCE_THRESHOLD", "0.9"))
    wake_word: str = "hey_jarvis"
    wake_threshold: float = float(os.getenv("WAKE_THRESHOLD", "0.4"))
    wake_noise_floor: float = float(os.getenv("WAKE_NOISE_FLOOR", "120"))
    push_to_talk_key: str = "`"
    mode_toggle_key: str = "tab"
    resume_session_id: str | None = os.getenv("MARVIS_RESUME_SESSION") or None


config = Config()

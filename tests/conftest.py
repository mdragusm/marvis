"""Stubs the heavy, model-loading dependencies (`faster_whisper`, `openwakeword`) before
anything imports `marvis.stt` / `marvis.wakeword`, so the suite exercises Marvis's own
logic without paying to load a Whisper model or a wakeword model, and without needing
those model files present at all. Must live in conftest.py so it runs before test
modules import anything under `marvis`."""

import sys
import types


def _install_fake_module(name: str, **attrs) -> None:
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    for attr_name, value in attrs.items():
        setattr(module, attr_name, value)
    sys.modules[name] = module


class _FakeWhisperModel:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def transcribe(self, audio):
        return [], None


class _FakeWakewordModel:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def predict(self, chunk):
        return {}


_install_fake_module("faster_whisper", WhisperModel=_FakeWhisperModel)
_install_fake_module("openwakeword", model=None)
_install_fake_module("openwakeword.model", Model=_FakeWakewordModel)

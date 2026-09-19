from marvis import tts
from marvis.config import config


def test_strip_markdown_removes_emphasis_markers():
    assert tts._strip_markdown("this is **bold** and _italic_") == "this is bold and italic"


def test_strip_markdown_keeps_words_separated_when_marker_has_no_surrounding_space():
    # Regression: a bare substitution (no space) glued "wordasterisk" pairs together.
    assert tts._strip_markdown("go*fast") == "go fast"


def test_strip_markdown_collapses_double_spaces_from_removed_markers():
    assert tts._strip_markdown("one **two** three") == "one two three"


def test_resolve_edge_voice_defaults_to_english_for_english_text(monkeypatch):
    monkeypatch.setattr(tts, "_utterance_voice", None)
    assert tts._resolve_edge_voice("What's the weather like today?") == config.edge_voice


def test_resolve_edge_voice_picks_spanish_above_confidence_threshold(monkeypatch):
    monkeypatch.setattr(tts, "_utterance_voice", None)
    monkeypatch.setattr(
        tts, "_lang_identifier", type("Fake", (), {"classify": staticmethod(lambda text: ("es", 0.95))})()
    )
    assert tts._resolve_edge_voice("cualquier texto") == config.edge_voice_es


def test_resolve_edge_voice_stays_english_below_confidence_threshold(monkeypatch):
    # Regression: langid's raw score isn't a 0-1 probability, so a barely-ahead "es"
    # guess on a short/ambiguous sentence must not be trusted.
    monkeypatch.setattr(tts, "_utterance_voice", None)
    monkeypatch.setattr(
        tts, "_lang_identifier", type("Fake", (), {"classify": staticmethod(lambda text: ("es", 0.4))})()
    )
    assert tts._resolve_edge_voice("ok") == config.edge_voice


def test_resolve_edge_voice_is_locked_for_the_rest_of_the_utterance(monkeypatch):
    # begin_utterance() resets the lock; _resolve_edge_voice must not re-classify
    # every sentence within the same reply, or voice could flip mid-reply.
    monkeypatch.setattr(tts, "_utterance_voice", "some-locked-voice")
    assert tts._resolve_edge_voice("cualquier texto en espanol") == "some-locked-voice"


def test_begin_utterance_clears_the_lock(monkeypatch):
    monkeypatch.setattr(tts, "_utterance_voice", "some-locked-voice")
    tts.begin_utterance()
    assert tts._utterance_voice is None

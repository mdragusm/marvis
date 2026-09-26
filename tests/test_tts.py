from marvis import tts
from marvis.config import config


def test_strip_markdown_removes_emphasis_markers():
    assert tts._strip_markdown("this is **bold** and _italic_") == "this is bold and italic"


def test_strip_markdown_keeps_words_separated_when_marker_has_no_surrounding_space():
    # Regression: a bare substitution (no space) glued "wordasterisk" pairs together.
    assert tts._strip_markdown("go*fast") == "go fast"


def test_strip_markdown_collapses_double_spaces_from_removed_markers():
    assert tts._strip_markdown("one **two** three") == "one two three"


def test_strip_markdown_removes_a_paired_code_fence():
    assert tts._strip_markdown("before ```code here``` after") == "before after"


def test_strip_markdown_reduces_a_lone_fence_marker_to_nothing():
    # Regression: assistant.py's sentence splitter treats a bare newline as a boundary, so a
    # fenced code block's opening/closing ``` line often arrives here on its own, with no
    # matching fence in the same call for the paired regex to catch. It must still be
    # stripped down to nothing rather than sent to speech synthesis as literal backticks --
    # see prepare()'s empty-text guard, added after this froze the live app.
    assert tts._strip_markdown("```") == ""


def test_strip_markdown_removes_horizontal_rules():
    # Regression: a --- separator sent as an isolated "sentence" (after the newline
    # splitter in assistant.py) was passed straight to edge-tts, which synthesises
    # silence / nothing for it -- confirmed live when a truncated reply ended on ---.
    assert tts._strip_markdown("---") == ""
    assert tts._strip_markdown("***") == ""
    assert tts._strip_markdown("___") == ""
    # Must not strip dashes that are part of real words or list items.
    assert tts._strip_markdown("step-by-step") == "step-by-step"


def test_strip_markdown_unwraps_inline_code_without_deleting_its_text():
    assert tts._strip_markdown("check `config.py` for it") == "check config.py for it"


def test_prepare_skips_synthesis_for_text_that_strips_to_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(tts, "_prepare_edge", lambda text: called.append(text))

    prepared = tts.prepare("```")

    assert called == []
    assert prepared.chunks.get() is None


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

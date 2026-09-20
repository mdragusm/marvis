import queue
import threading

from marvis import assistant
from marvis.assistant import _is_clear_command, _is_likely_hallucination, _report_turn_failure, _split_sentences


def test_split_sentences_splits_on_terminal_punctuation():
    sentences, remainder = _split_sentences("Hello there. How are you? Fine!")
    assert sentences == ["Hello there.", "How are you?"]
    assert remainder == "Fine!"


def test_split_sentences_splits_on_bare_newline():
    # Regression: a line break with no preceding punctuation used to be swallowed
    # as an ordinary space, producing no audible pause between lines.
    sentences, remainder = _split_sentences("First line\nSecond line")
    assert sentences == ["First line"]
    assert remainder == "Second line"


def test_split_sentences_leaves_incomplete_sentence_in_the_remainder():
    sentences, remainder = _split_sentences("This is still being generated")
    assert sentences == []
    assert remainder == "This is still being generated"


def test_split_sentences_does_not_treat_an_ellipsis_as_a_sentence_end():
    # Regression: reported directly (a live debug log caught it mid-incident) -- an ellipsis
    # inside running text (e.g. quoting "me... speculating") was matched by the old regex as
    # a sentence boundary, so _run_turn spoke the fragment before it in total isolation, on
    # its own separate TTS call, before continuing with the rest of the thought.
    sentences, remainder = _split_sentences('That fits - "me... speculating" is the idea. Next.')
    assert sentences == ['That fits - "me... speculating" is the idea.']
    assert remainder == "Next."


def test_split_sentences_still_splits_a_real_sentence_after_an_ellipsis():
    sentences, remainder = _split_sentences("Wait... Really? Yes. And more.")
    assert sentences == ["Wait... Really?", "Yes."]
    assert remainder == "And more."


def test_split_sentences_is_stable_across_repeated_calls_on_the_same_buffer():
    # _run_turn calls this once per streamed chunk, re-passing whatever remainder
    # came back last time -- it must not re-split or duplicate already-returned sentences.
    sentences, remainder = _split_sentences("One. Two")
    assert sentences == ["One."]
    more_sentences, remainder = _split_sentences(remainder + " is still going")
    assert more_sentences == []
    assert remainder == "Two is still going"


def test_is_clear_command_matches_known_phrases_case_insensitively():
    assert _is_clear_command("Start Over")
    assert _is_clear_command("forget everything we've talked about")


def test_is_clear_command_ignores_trailing_punctuation():
    assert _is_clear_command("clear the context.")
    assert _is_clear_command("start over!")


def test_is_clear_command_rejects_unrelated_text():
    assert not _is_clear_command("what's the weather like")
    assert not _is_clear_command("")


def test_is_likely_hallucination_matches_known_phrases_case_insensitively():
    assert _is_likely_hallucination("Thank you.")
    assert _is_likely_hallucination("Thank you very much.")
    assert _is_likely_hallucination("thanks for watching")


def test_is_likely_hallucination_ignores_trailing_punctuation():
    assert _is_likely_hallucination("Thank you!")


def test_is_likely_hallucination_rejects_unrelated_text():
    assert not _is_likely_hallucination("what's the weather like")
    assert not _is_likely_hallucination("")
    # A real "thank you" as part of a longer sentence must still go through.
    assert not _is_likely_hallucination("thank you for doing that yesterday")


def test_report_turn_failure_speaks_and_records_a_fallback_message(monkeypatch):
    # Regression: an exception in _run_turn outside the LLM call path (e.g. a mic/transcription
    # error) used to be logged to marvis_error.log and otherwise leave the user with silence and
    # no indication anything happened. This pins that a fallback message is spoken and recorded
    # the same way a real reply would be.
    calls = []
    monkeypatch.setattr(assistant, "begin_utterance", lambda: calls.append("begin_utterance"))
    monkeypatch.setattr(assistant.indicator, "start_marvis_message", lambda: calls.append("start_marvis_message"))
    monkeypatch.setattr(
        assistant.indicator, "append_marvis_message", lambda text: calls.append(("append_marvis_message", text))
    )
    monkeypatch.setattr(assistant, "get_session_id", lambda: "session-1")
    monkeypatch.setattr(
        assistant.history, "append_message", lambda sid, role, text: calls.append(("append_message", sid, role, text))
    )
    monkeypatch.setattr(assistant, "prepare", lambda text: ("prepared", text))
    monkeypatch.setattr(assistant, "play", lambda prepared: calls.append(("play", prepared)))
    assistant.interrupt_event.set()

    _report_turn_failure()

    assert not assistant.interrupt_event.is_set()
    assert calls == [
        "begin_utterance",
        "start_marvis_message",
        ("append_marvis_message", assistant._TURN_FAILURE_MESSAGE),
        ("append_message", "session-1", "marvis", assistant._TURN_FAILURE_MESSAGE),
        ("play", ("prepared", assistant._TURN_FAILURE_MESSAGE)),
    ]


def test_generate_and_apply_title_saves_and_pushes_the_title(monkeypatch):
    monkeypatch.setattr(assistant, "generate_title", lambda text: "Trip Planning")
    saved = []
    pushed = []
    monkeypatch.setattr(assistant.history, "save_title", lambda sid, title: saved.append((sid, title)))
    monkeypatch.setattr(assistant.indicator, "set_tab_label", lambda sid, label: pushed.append((sid, label)))

    assistant._generate_and_apply_title("session-1", "help me plan a trip")

    assert saved == [("session-1", "Trip Planning")]
    assert pushed == [("session-1", "Trip Planning")]


def test_generate_and_apply_title_does_nothing_when_generation_fails(monkeypatch):
    monkeypatch.setattr(assistant, "generate_title", lambda text: None)
    saved = []
    monkeypatch.setattr(assistant.history, "save_title", lambda sid, title: saved.append((sid, title)))

    assistant._generate_and_apply_title("session-1", "hello")

    assert saved == []


def _raise_prepare_error(sentence):
    raise RuntimeError("boom")


def test_enqueue_releases_the_prepare_gate_when_prepare_raises(monkeypatch):
    # Regression: _prepare_gate.acquire() is only ever released by _speech_worker once it
    # plays the corresponding item. If prepare() itself raises, nothing gets queued for it to
    # play, so the permit must be released in _enqueue's except branch or it leaks -- after
    # _PIPELINE_DEPTH leaks, every future turn's _enqueue() blocks forever.
    monkeypatch.setattr(assistant, "prepare", _raise_prepare_error)
    q: "queue.Queue" = queue.Queue()

    def call_enqueue_more_times_than_the_gate_has_permits():
        # Each call stands in for a separate turn -- in the real app a raised exception here
        # propagates up to run()'s own try/except, so the next turn always calls _enqueue()
        # again regardless of whether the previous one leaked a permit.
        for _ in range(assistant._PIPELINE_DEPTH + 1):
            try:
                assistant._enqueue(q, "sentence")
            except Exception:
                pass

    thread = threading.Thread(target=call_enqueue_more_times_than_the_gate_has_permits, daemon=True)
    thread.start()
    thread.join(timeout=2)

    assert not thread.is_alive()  # still blocked on the gate if a permit leaked
    assert q.empty()

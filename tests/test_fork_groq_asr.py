"""groq_asr.py and the groq backend's fallback (fork addition). No network."""
import pytest

import groq_asr
import transcribe_backends

ANSWER = {
    "text": "Hello there. 你好",
    "language": "English",
    "segments": [{"start": 0.0, "end": 1.2, "text": " Hello there."},
                 {"start": 1.2, "end": 2.0, "text": " 你好"}],
    "words": [{"word": "Hello", "start": 0.0, "end": 0.5},
              {"word": "there.", "start": 0.5, "end": 1.1},
              {"word": "你好", "start": 1.3, "end": 1.9}],
}


def test_to_transcript_matches_the_local_whisper_shape():
    t = groq_asr.to_transcript(ANSWER)
    assert t["language"] == "en"
    assert [w["word"] for w in t["segments"][0]["words"]] == [" Hello", " there."]
    assert t["segments"][1]["words"][0] == {"word": " 你好", "start": 1.3, "end": 1.9}


@pytest.mark.parametrize("raw,code", [("japanese", "ja"), ("en-US", "en"), ("pt_BR", "pt"),
                                      (None, None), ("", None)])
def test_language_code(raw, code):
    assert groq_asr.language_code(raw) == code


def test_no_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(groq_asr.GroqUnavailable):
        groq_asr.transcribe("whatever.mp4")


def test_backend_falls_back_to_local_whisper(monkeypatch):
    monkeypatch.setenv("TRANSCRIBE_BACKEND", "groq")
    monkeypatch.setattr(transcribe_backends, "_has_audio_stream", lambda p: True)

    def refused(path, timeout=600):
        raise groq_asr.GroqUnavailable("HTTP 429")

    local = {"text": "local", "language": "en", "segments": []}
    monkeypatch.setattr(groq_asr, "transcribe", refused)
    monkeypatch.setattr(transcribe_backends, "_transcribe_with_whisper", lambda p: local)
    assert transcribe_backends.transcribe_media("x.mp4") is local


def test_backend_uses_groq_when_it_answers(monkeypatch):
    monkeypatch.setenv("TRANSCRIBE_BACKEND", "groq")
    monkeypatch.setattr(transcribe_backends, "_has_audio_stream", lambda p: True)
    monkeypatch.setattr(groq_asr, "transcribe", lambda p, timeout=600: groq_asr.to_transcript(ANSWER))
    monkeypatch.setattr(transcribe_backends, "_transcribe_with_whisper",
                        lambda p: pytest.fail("local whisper must not run"))
    assert transcribe_backends.transcribe_media("x.mp4")["language"] == "en"

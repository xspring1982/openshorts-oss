"""Hosted Whisper through Groq's API (fork addition).

TRANSCRIBE_BACKEND=groq with GROQ_API_KEY transcribes with Groq's
whisper-large-v3-turbo (seconds for an hour of audio, no GPU needed) and
returns the same transcript shape as the local faster-whisper backend.
transcribe_backends.transcribe_media falls back to local faster-whisper on
any failure: no key, a rate limit or quota (Groq's free tier caps the audio
per day), an outage, or audio over the upload limit. Set WHISPER_MODEL=
large-v3-turbo (and WHISPER_DEVICE=cuda on a GPU) so the fallback is the
same model.

Standard library plus ffmpeg.
"""
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
MAX_UPLOAD_BYTES = 24 * 1024 * 1024  # under Groq's 25 MB file limit

LANGUAGE_CODES = {
    "afrikaans": "af", "albanian": "sq", "arabic": "ar", "basque": "eu", "belarusian": "be",
    "bengali": "bn", "bosnian": "bs", "bulgarian": "bg", "catalan": "ca", "chinese": "zh",
    "croatian": "hr", "czech": "cs", "danish": "da", "dutch": "nl", "english": "en",
    "estonian": "et", "finnish": "fi", "french": "fr", "galician": "gl", "german": "de",
    "greek": "el", "hebrew": "he", "hindi": "hi", "hungarian": "hu", "icelandic": "is",
    "indonesian": "id", "italian": "it", "japanese": "ja", "kazakh": "kk", "korean": "ko",
    "latvian": "lv", "lithuanian": "lt", "macedonian": "mk", "malay": "ms", "marathi": "mr",
    "nepali": "ne", "norwegian": "no", "nynorsk": "nn", "persian": "fa", "polish": "pl",
    "portuguese": "pt", "romanian": "ro", "russian": "ru", "serbian": "sr", "slovak": "sk",
    "slovenian": "sl", "spanish": "es", "swahili": "sw", "swedish": "sv", "tagalog": "tl",
    "thai": "th", "turkish": "tr", "ukrainian": "uk", "urdu": "ur", "vietnamese": "vi",
    "welsh": "cy", "tamil": "ta", "telugu": "te", "kannada": "kn", "malayalam": "ml",
    "gujarati": "gu", "punjabi": "pa", "sinhala": "si", "khmer": "km", "lao": "lo",
    "burmese": "my", "myanmar": "my", "amharic": "am", "georgian": "ka", "armenian": "hy",
    "mongolian": "mn", "pashto": "ps", "sindhi": "sd", "somali": "so", "yoruba": "yo",
}


class GroqUnavailable(RuntimeError):
    """Groq did not transcribe (no key, limit, outage, too large)."""


def language_code(raw):
    """ISO code from a Whisper language name ("japanese") or a tag ("en-US")."""
    if not raw:
        return None
    t = str(raw).strip().lower()
    if t in LANGUAGE_CODES:
        return LANGUAGE_CODES[t]
    t = t.replace("_", "-").split("-")[0]
    return t if 2 <= len(t) <= 3 else None


def _extract_mp3(media_path):
    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="groq_")
    os.close(fd)
    res = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", media_path, "-vn", "-ac", "1",
                          "-ar", "16000", "-b:a", "48k", path], capture_output=True, timeout=1800)
    if res.returncode != 0:
        os.unlink(path)
        raise GroqUnavailable("ffmpeg could not extract the audio")
    return path


def _post(audio_bytes, key, timeout):
    boundary = f"----openshorts{int(time.time() * 1000)}"
    fields = [("model", MODEL), ("response_format", "verbose_json"),
              ("timestamp_granularities[]", "word"), ("timestamp_granularities[]", "segment")]
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}\r\n'.encode()
             for n, v in fields]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.mp3"\r\n'
                 f"Content-Type: audio/mpeg\r\n\r\n".encode() + audio_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        ENDPOINT, data=b"".join(parts), method="POST",
        headers={"Authorization": f"Bearer {key}", "User-Agent": "openshorts",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as e:
        raise GroqUnavailable(f"HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise GroqUnavailable(type(e).__name__) from None


def to_transcript(data):
    """Groq's verbose_json -> {"text", "language", "segments": [{start, end,
    text, words: [{word, start, end}]}]}, words with a leading space like
    faster-whisper's."""
    words = data.get("words") or []
    segments, i = [], 0
    for seg in data.get("segments") or []:
        seg_words = []
        while i < len(words) and words[i]["start"] < seg["end"] - 1e-6:
            w = words[i]
            seg_words.append({"word": " " + str(w["word"]).strip(),
                              "start": float(w["start"]), "end": float(w["end"])})
            i += 1
        segments.append({"start": float(seg["start"]), "end": float(seg["end"]),
                         "text": seg["text"], "words": seg_words})
    if segments and i < len(words):
        for w in words[i:]:
            segments[-1]["words"].append({"word": " " + str(w["word"]).strip(),
                                          "start": float(w["start"]), "end": float(w["end"])})
    return {"text": data.get("text", ""), "language": language_code(data.get("language")) or "en",
            "segments": segments}


def transcribe(media_path, timeout=600):
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise GroqUnavailable("GROQ_API_KEY is not set")
    mp3 = _extract_mp3(media_path)
    try:
        size = os.path.getsize(mp3)
        if size > MAX_UPLOAD_BYTES:
            raise GroqUnavailable(f"audio is {size / 1e6:.0f} MB, over the upload limit")
        with open(mp3, "rb") as f:
            data = _post(f.read(), key, timeout)
    finally:
        os.unlink(mp3)
    transcript = to_transcript(data)
    if not any(s["words"] for s in transcript["segments"]):
        raise GroqUnavailable("no words in the answer")
    return transcript

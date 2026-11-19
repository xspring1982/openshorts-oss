"""Audio-first partial download for YouTube links (fork addition).

A URL job normally downloads the whole video before anything else happens.
For a long video most of those bytes are never used: the clips cover a few
minutes. With AUDIO_FIRST=1 (or ``main.py --audio-first``) a YouTube job:

1. extracts the video's info (no media yet);
2. downloads only the audio track (speech quality, ~0.4 MB per minute),
   as parallel byte ranges when the stream is a plain file of known size;
3. transcribes it and asks the LLM for the moments (main.py's own steps);
4. fetches only the picked ranges of the video: YouTube's DASH MP4 streams
   carry a segment index (sidx), so the bytes of any time range are known up
   front and come down as plain HTTP range requests at full speed
   (yt-dlp's --download-sections streams through ffmpeg at about real time);
5. muxes each range with the matching stretch of the audio already on disk.

Each clip is then cut from the range file that holds it (``section_for``).
Whenever a step cannot be done (no DASH stream, a refused range, speech too
thin to pick from the transcript), ``fetch`` says so and main.py downloads
the whole video as usual, keeping the transcript and picks already made.

On openshorts.io (same code, November 2026, through a proxy) a 20-minute
720p60 video took 12 min 17 s to download whole; the first two audio-first
jobs after the switch fetched what they needed in 98 s and 182 s.

Standard library plus yt-dlp (already an upstream dependency) and ffmpeg.
"""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

SECTION_PAD = float(os.environ.get("SECTION_PAD", "2"))
SECTION_PARALLEL = max(1, int(os.environ.get("SECTION_PARALLEL", "6")))
MAX_AUDIO_ABR = 72  # kbps: opus 50k or AAC 48k is plenty for speech


class UrlExpired(RuntimeError):
    """The media URL was refused (403/410): bound to another IP, or expired."""


@dataclass
class Result:
    """What the audio-first fetch produced. ``source`` is None when the video
    must still be downloaded whole; ``transcript`` and ``clips`` are then
    whatever was already made (reuse them)."""
    title: str
    duration: float
    transcript: dict | None = None
    clips: dict | None = None
    source: str | None = None
    sections: list[dict] = field(default_factory=list)
    reason: str = ""


def enabled(flag: bool = False, env=None) -> bool:
    env = os.environ if env is None else env
    return flag or str(env.get("AUDIO_FIRST", "")).strip().lower() in ("1", "true", "yes")


def log(msg: str) -> None:
    print(f"🎧 [audio-first] {msg}", flush=True)


# ---- info and formats -------------------------------------------------------

def extract(url: str) -> dict:
    """yt-dlp's info for one video, without downloading media."""
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.sanitize_info(ydl.extract_info(url, download=False))


def pick_audio(info: dict) -> dict | None:
    """Audio-only format for speech: the best one at most MAX_AUDIO_ABR kbps,
    else the smallest. Original-language tracks first (dubbed videos list one
    track per language)."""
    audio = [f for f in info.get("formats") or []
             if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")
             and f.get("url")]
    if not audio:
        return None

    def original(f):
        # yt-dlp ranks the original track's language_preference highest.
        return -(f.get("language_preference") or 0)

    small = [f for f in audio if (f.get("abr") or 0) and f["abr"] <= MAX_AUDIO_ABR]
    if small:
        return sorted(small, key=lambda f: (original(f), -(f.get("abr") or 0)))[0]
    return sorted(audio, key=lambda f: (original(f), f.get("abr") or 1e9))[0]


def pick_dash_video(info: dict) -> dict | None:
    """The <=720p H.264 video-only DASH MP4 stream with the highest bitrate."""
    best = None
    for f in info.get("formats") or []:
        if (f.get("ext") == "mp4" and f.get("protocol") == "https" and f.get("acodec") == "none"
                and str(f.get("vcodec") or "").startswith("avc1")
                and (f.get("height") or 0) <= 720 and f.get("url")):
            key = (f.get("height") or 0, f.get("tbr") or 0)
            if best is None or key > (best.get("height") or 0, best.get("tbr") or 0):
                best = f
    return best


# ---- byte ranges ----------------------------------------------------------------

def fetch_range(url: str, headers: dict, first: int, last: int, timeout: int = 120,
                attempts: int = 3, allow_short: bool = False) -> bytes:
    """Bytes first..last of url. Retries dropped connections; a 403/410 is
    raised as UrlExpired at once (YouTube media URLs are bound to the IP that
    extracted them, so retrying the same URL cannot help). allow_short
    accepts fewer bytes when the file ends before ``last``."""
    req = urllib.request.Request(url, headers={**headers, "Range": f"bytes={first}-{last}"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                data = _read_by(res, time.monotonic() + 2 * timeout)
            if len(data) != last - first + 1 and not (allow_short and data):
                raise RuntimeError(f"short range {len(data)} of {last - first + 1}")
            return data
        except urllib.error.HTTPError as e:
            if e.code in (403, 410):
                raise UrlExpired(f"HTTP {e.code}") from None
            err = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, RuntimeError,
                http.client.HTTPException) as e:
            # IncompleteRead (a dropped connection mid-range) is an
            # HTTPException, not an OSError. Never the URL in the message.
            err = f"{type(e).__name__}: {getattr(e, 'reason', '') or ''}"
        if attempt == attempts - 1:
            raise RuntimeError(f"range fetch failed: {err}")
        time.sleep(2 + 3 * attempt)
    raise AssertionError("unreachable")


def _read_by(res, deadline: float) -> bytes:
    """The whole body, or TimeoutError once the deadline passes (the socket
    timeout only bounds each read, and a trickling server can stall a job)."""
    parts = []
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("range read took too long")
        chunk = res.read(256 * 1024)
        if not chunk:
            return b"".join(parts)
        parts.append(chunk)


def parallel_fetch(url: str, headers: dict, size: int, out: Path, parts: int = 6) -> None:
    """A file of known size as `parts` byte ranges at once."""
    step = max(512 * 1024, -(-size // parts))
    spans = [(o, min(o + step, size) - 1) for o in range(0, size, step)]
    with ThreadPoolExecutor(max_workers=parts) as pool:
        chunks = list(pool.map(lambda r: fetch_range(url, headers, *r, timeout=300), spans))
    out.write_bytes(b"".join(chunks))


def fetch_audio(url: str, info: dict, dest: Path) -> Path:
    """The speech-quality audio track: parallel ranges when possible, else
    yt-dlp's own download of the same format."""
    fmt = pick_audio(info)
    if fmt is None:
        raise RuntimeError("no audio-only format")
    size = int(fmt.get("filesize") or 0)
    out = dest / f"audio_first.{fmt.get('ext') or 'webm'}"
    if fmt.get("protocol") == "https" and size:
        try:
            parallel_fetch(fmt["url"], dict(fmt.get("http_headers") or {}), size, out)
            return out
        except RuntimeError as e:
            log(f"parallel audio fetch failed ({e}); using yt-dlp")
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
            "format": fmt["format_id"], "outtmpl": str(dest / "audio_first.%(ext)s")}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = sorted(p for p in dest.glob("audio_first.*") if not p.name.endswith(".part"))
    if not files:
        raise RuntimeError("no audio after download")
    return files[0]


# ---- sections -------------------------------------------------------------------

def section_ranges(shorts: list[dict], duration: float, pad: float = SECTION_PAD,
                   merge_gap: float = 8.0) -> list[tuple[float, float]]:
    """Padded clip ranges, merged when they overlap or nearly touch (one
    request instead of two, and no frame fetched twice)."""
    spans = sorted((max(0.0, float(s["start"]) - pad),
                    min(duration or float("inf"), float(s["end"]) + pad))
                   for s in shorts if float(s.get("end", 0)) > float(s.get("start", 0)))
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + merge_gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(round(a, 2), round(b, 2)) for a, b in merged]


def _boxes(data: bytes, start: int = 0):
    i = start
    while i + 8 <= len(data):
        size = int.from_bytes(data[i:i + 4], "big")
        kind = data[i + 4:i + 8].decode("latin-1")
        if size == 1:
            size = int.from_bytes(data[i + 8:i + 16], "big")
        if size < 8:
            return
        yield kind, i, size
        i += size


def parse_sidx(head: bytes) -> tuple[int, list[tuple[float, float, int, int]]]:
    """(init length, [(start s, end s, first byte, last byte)]) of a DASH MP4
    from its first bytes (ftyp + moov + sidx)."""
    init_end, sidx = 0, None
    for kind, at, size in _boxes(head):
        if kind in ("ftyp", "moov"):
            init_end = at + size
        elif kind == "sidx":
            sidx = (at, size)
            break
    if not sidx or not init_end:
        raise RuntimeError("no segment index")
    at, size = sidx
    b = head[at:at + size]
    if len(b) < size:
        raise RuntimeError("segment index cut off")
    version = b[8]
    timescale = int.from_bytes(b[16:20], "big")
    if version == 0:
        ept, first_off, p = int.from_bytes(b[20:24], "big"), int.from_bytes(b[24:28], "big"), 28
    else:
        ept, first_off, p = int.from_bytes(b[20:28], "big"), int.from_bytes(b[28:36], "big"), 36
    count = int.from_bytes(b[p + 2:p + 4], "big")
    p += 4
    offset, t = at + size + first_off, ept
    segments = []
    for _ in range(count):
        ref = int.from_bytes(b[p:p + 4], "big") & 0x7FFFFFFF
        dur = int.from_bytes(b[p + 4:p + 8], "big")
        segments.append((t / timescale, (t + dur) / timescale, offset, offset + ref - 1))
        offset += ref
        t += dur
        p += 12
    return init_end, segments


def _ffmpeg(args: list[str], timeout: int = 600) -> None:
    res = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True,
                         text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {res.stderr[-500:]}")


def ranged_sections(info: dict, audio: Path, ranges: list[tuple[float, float]],
                    dest: Path) -> list[dict]:
    """Each range as its own file: the video segments that cover it, fetched
    by byte range, muxed with the matching stretch of the audio already on
    disk. A file starts on a segment boundary (a keyframe), so its recorded
    start is exact, not the asked-for one."""
    fmt = pick_dash_video(info)
    if not fmt:
        raise RuntimeError("no DASH H.264 stream")
    headers = dict(fmt.get("http_headers") or {})
    head = fetch_range(fmt["url"], headers, 0, 256 * 1024 - 1, allow_short=True)
    init_end, segments = parse_sidx(head)
    init = head[:init_end]

    def one(i: int, a: float, b: float) -> dict:
        chosen = [s for s in segments if s[1] > a and s[0] < b]
        if not chosen:
            raise RuntimeError(f"no segments for {a}-{b}")
        t0, t1 = chosen[0][0], chosen[-1][1]
        first, last = chosen[0][2], chosen[-1][3]
        chunk = 4 * 1024 * 1024
        parts = [fetch_range(fmt["url"], headers, o, min(o + chunk - 1, last))
                 for o in range(first, last + 1, chunk)]
        video = dest / f"section_{i}.video.mp4"
        video.write_bytes(init + b"".join(parts))
        out = dest / f"section_{i}.mp4"
        _ffmpeg(["-i", str(video), "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", str(audio),
                 "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                 "-movflags", "+faststart", str(out)])
        video.unlink(missing_ok=True)
        return {"path": str(out), "start": round(t0, 3), "end": round(t1, 3)}

    with ThreadPoolExecutor(max_workers=SECTION_PARALLEL) as pool:
        return list(pool.map(lambda r: one(*r), [(i, a, b) for i, (a, b) in enumerate(ranges)]))


def join_sections(sections: list[dict], dest: Path) -> str:
    """One file of all ranges, for the source-wide checks main.py makes on
    sample frames (burned-in subtitles, layout). Clips are cut from the
    section files themselves (section_for)."""
    if len(sections) == 1:
        return sections[0]["path"]
    listing = dest / "sections.txt"
    listing.write_text("".join(f"file '{s['path']}'\n" for s in sections))
    out = dest / "source_sections.mp4"
    try:
        _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)])
        return str(out)
    except RuntimeError as e:
        log(f"section join failed, using the first: {e}")
        return sections[0]["path"]


def section_for(sections: list[dict] | None, input_video: str, start: float,
                end: float) -> tuple[str, float]:
    """(file, its start time in the source) that holds [start, end]: one of
    the fetched ranges, else the whole source."""
    if not sections:
        return input_video, 0.0
    for s in sections:
        if float(s["start"]) <= float(start) + 0.05 and float(end) <= float(s["end"]) + 0.05:
            return s["path"], float(s["start"])
    raise RuntimeError(f"no fetched section holds {start}-{end}")


# ---- the whole step ---------------------------------------------------------------

def fetch(url: str, output_dir: str, transcribe, pick, sparse) -> Result | None:
    """Audio -> transcript -> picks -> only the picked ranges in video.

    transcribe(audio_path) -> transcript; pick(transcript, duration) -> clips
    data ({"shorts": [...]}); sparse(transcript, duration) -> True when speech
    is too thin to pick from. Returns None when nothing was made (then
    main.py downloads as usual), else a Result (see its docstring)."""
    dest = Path(output_dir)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        info = extract(url)
    except Exception as e:  # noqa: BLE001 - the normal download reports it properly
        log(f"info extraction failed ({type(e).__name__}); downloading as usual")
        return None
    if info.get("is_live") or info.get("live_status") == "is_live" or info.get("_type") == "playlist":
        return None
    duration = float(info.get("duration") or 0)
    title = str(info.get("title") or "youtube_video")
    if not duration:
        return None
    started = time.monotonic()
    try:
        audio = fetch_audio(url, info, dest)
    except Exception as e:  # noqa: BLE001
        log(f"audio fetch failed ({e}); downloading as usual")
        return None
    log(f"audio in {time.monotonic() - started:.0f}s ({audio.stat().st_size / 1e6:.1f} MB)")
    try:
        transcript = transcribe(str(audio))
        if sparse(transcript, duration):
            return Result(title, duration, transcript, reason="speech too thin")
        clips = pick(transcript, duration)
        if not clips or not clips.get("shorts"):
            return Result(title, duration, transcript, reason="no picks")
        ranges = section_ranges(clips["shorts"], duration)
        try:
            sections = ranged_sections(info, audio, ranges, dest)
        except (RuntimeError, ValueError, KeyError) as e:
            log(f"ranged fetch failed ({e}); downloading the whole video")
            for p in dest.glob("section_*"):
                p.unlink(missing_ok=True)
            return Result(title, duration, transcript, clips, reason=str(e))
    finally:
        audio.unlink(missing_ok=True)
    got = sum(s["end"] - s["start"] for s in sections)
    log(f"fetched {len(sections)} range(s), {got:.0f}s of {duration:.0f}s, "
        f"in {time.monotonic() - started:.0f}s")
    (dest / "sections.json").write_text(json.dumps(sections))
    return Result(title, duration, transcript, clips, join_sections(sections, dest), sections)

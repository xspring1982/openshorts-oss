"""audio_first.py (fork addition): no network beyond a local test server."""
import http.server
import threading
from pathlib import Path

import pytest

import audio_first as af


def test_enabled_by_flag_or_env():
    assert af.enabled(True, {}) is True
    assert af.enabled(False, {"AUDIO_FIRST": "1"}) is True
    assert af.enabled(False, {"AUDIO_FIRST": "0"}) is False
    assert af.enabled(False, {}) is False


def test_section_ranges_pad_merge_and_clamp():
    shorts = [{"start": 100, "end": 130}, {"start": 135, "end": 160}, {"start": 400, "end": 420},
              {"start": 0.5, "end": 20}, {"start": 50, "end": 40}]
    assert af.section_ranges(shorts, 421.0) == [(0.0, 22.0), (98.0, 162.0), (398.0, 421.0)]


def test_pick_audio_prefers_speech_quality_original_track():
    info = {"formats": [
        {"format_id": "hi", "vcodec": "none", "acodec": "opus", "abr": 160, "url": "u"},
        {"format_id": "dub", "vcodec": "none", "acodec": "opus", "abr": 50, "url": "u",
         "language_preference": -1},
        {"format_id": "orig", "vcodec": "none", "acodec": "opus", "abr": 50, "url": "u",
         "language_preference": 10},
        {"format_id": "lo", "vcodec": "none", "acodec": "mp4a", "abr": 30, "url": "u"},
        {"format_id": "v", "vcodec": "avc1", "acodec": "none", "url": "u"},
    ]}
    assert af.pick_audio(info)["format_id"] == "orig"
    only_big = {"formats": [{"format_id": "a", "vcodec": "none", "acodec": "opus", "abr": 160, "url": "u"},
                            {"format_id": "b", "vcodec": "none", "acodec": "opus", "abr": 128, "url": "u"}]}
    assert af.pick_audio(only_big)["format_id"] == "b"
    assert af.pick_audio({"formats": []}) is None


def test_pick_dash_video_takes_the_best_h264_up_to_720p():
    base = {"ext": "mp4", "protocol": "https", "acodec": "none", "url": "u"}
    info = {"formats": [
        {**base, "format_id": "1080", "vcodec": "avc1.640028", "height": 1080, "tbr": 4000},
        {**base, "format_id": "720", "vcodec": "avc1.4d401f", "height": 720, "tbr": 2000},
        {**base, "format_id": "720vp9", "vcodec": "vp9", "height": 720, "tbr": 1500},
        {**base, "format_id": "480", "vcodec": "avc1.4d401e", "height": 480, "tbr": 900},
        {**base, "format_id": "hls", "vcodec": "avc1", "height": 720, "protocol": "m3u8"},
    ]}
    assert af.pick_dash_video(info)["format_id"] == "720"
    assert af.pick_dash_video({"formats": []}) is None


def _box(kind: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def _sidx(timescale: int, refs: list[tuple[int, int]], first_offset: int = 0) -> bytes:
    body = bytes([0, 0, 0, 0])                      # version 0, flags
    body += (1).to_bytes(4, "big")                  # reference_ID
    body += timescale.to_bytes(4, "big")
    body += (0).to_bytes(4, "big")                  # earliest presentation time
    body += first_offset.to_bytes(4, "big")
    body += (0).to_bytes(2, "big") + len(refs).to_bytes(2, "big")
    for size, dur in refs:
        body += size.to_bytes(4, "big") + dur.to_bytes(4, "big") + (0x90000000).to_bytes(4, "big")
    return _box(b"sidx", body)


def test_parse_sidx_maps_time_to_bytes():
    head = _box(b"ftyp", b"isom" * 4) + _box(b"moov", b"\0" * 40)
    init_len = len(head)
    sidx = _sidx(1000, [(5000, 2000), (7000, 3000), (6000, 2500)])
    init_end, segments = af.parse_sidx(head + sidx)
    assert init_end == init_len
    first = init_len + len(sidx)
    assert segments == [
        (0.0, 2.0, first, first + 4999),
        (2.0, 5.0, first + 5000, first + 11999),
        (5.0, 7.5, first + 12000, first + 17999),
    ]


def test_parse_sidx_without_an_index_fails_clearly():
    with pytest.raises(RuntimeError, match="no segment index"):
        af.parse_sidx(_box(b"ftyp", b"isom") + _box(b"moov", b"\0" * 8))


def test_section_for():
    sections = [{"path": "/s0.mp4", "start": 98.0, "end": 162.5},
                {"path": "/s1.mp4", "start": 397.9, "end": 421.0}]
    assert af.section_for(sections, "/whole.mp4", 100, 130) == ("/s0.mp4", 98.0)
    assert af.section_for(sections, "/whole.mp4", 398.0, 421.0) == ("/s1.mp4", 397.9)
    assert af.section_for(None, "/whole.mp4", 5, 9) == ("/whole.mp4", 0.0)
    with pytest.raises(RuntimeError):
        af.section_for(sections, "/whole.mp4", 200, 230)


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    data = bytes(range(256)) * 64

    def do_GET(self):  # noqa: N802
        if self.path == "/gone":
            self.send_response(403)
            self.end_headers()
            return
        first, last = (int(x) for x in self.headers["Range"].split("=")[1].split("-"))
        body = self.data[first:last + 1]
        self.send_response(206)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_fetch_range_and_parallel_fetch(server, tmp_path):
    assert af.fetch_range(server + "/f", {}, 10, 19) == _RangeHandler.data[10:20]
    out = tmp_path / "f.bin"
    af.parallel_fetch(server + "/f", {}, len(_RangeHandler.data), out, parts=3)
    assert out.read_bytes() == _RangeHandler.data


def test_refused_url_is_url_expired_without_retrying(server):
    with pytest.raises(af.UrlExpired):
        af.fetch_range(server + "/gone", {}, 0, 9)


# ---- fetch(): the whole step, with the network parts replaced ----------------

INFO = {"title": "A talk", "duration": 600.0, "formats": []}
TRANSCRIPT = {"language": "en", "segments": [{"start": 0, "end": 5, "text": "hi"}]}
CLIPS = {"shorts": [{"start": 100, "end": 130}, {"start": 300, "end": 340}]}


@pytest.fixture
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(af, "extract", lambda url: dict(INFO))

    def fake_audio(url, info, dest):
        p = Path(dest) / "audio_first.webm"
        p.write_bytes(b"a")
        return p

    monkeypatch.setattr(af, "fetch_audio", fake_audio)
    return tmp_path


def _call(dest, sparse=False, clips=CLIPS):
    return af.fetch("https://youtube.com/watch?v=x", str(dest), transcribe=lambda p: TRANSCRIPT,
                    pick=lambda t, d: clips, sparse=lambda t, d: sparse)


def test_fetch_success_returns_sections_and_reuses_picks(offline, monkeypatch):
    def fake_sections(info, audio, ranges, dest):
        assert ranges == [(98.0, 132.0), (298.0, 342.0)]
        return [{"path": str(dest / f"section_{i}.mp4"), "start": a, "end": b}
                for i, (a, b) in enumerate(ranges)]

    monkeypatch.setattr(af, "ranged_sections", fake_sections)
    monkeypatch.setattr(af, "join_sections", lambda s, d: str(d / "source_sections.mp4"))
    r = _call(offline)
    assert r.source.endswith("source_sections.mp4") and len(r.sections) == 2
    assert r.transcript is TRANSCRIPT and r.clips is CLIPS and r.duration == 600.0
    assert (offline / "sections.json").exists()
    assert not (offline / "audio_first.webm").exists()  # audio cleaned up


def test_fetch_falls_back_but_keeps_transcript_and_picks(offline, monkeypatch):
    def broken(*a):
        raise RuntimeError("no DASH H.264 stream")

    monkeypatch.setattr(af, "ranged_sections", broken)
    r = _call(offline)
    assert r.source is None and r.transcript is TRANSCRIPT and r.clips is CLIPS


def test_thin_speech_keeps_only_the_transcript(offline):
    r = _call(offline, sparse=True)
    assert r.source is None and r.clips is None and r.transcript is TRANSCRIPT


def test_no_picks_keeps_only_the_transcript(offline):
    r = _call(offline, clips={"shorts": []})
    assert r.source is None and r.clips is None


def test_live_streams_and_failed_extraction_return_none(monkeypatch, tmp_path):
    monkeypatch.setattr(af, "extract", lambda url: {**INFO, "is_live": True})
    assert _call(tmp_path) is None

    def boom(url):
        raise RuntimeError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(af, "extract", boom)
    assert _call(tmp_path) is None


# ---- ranged_sections() end to end on a real DASH file ------------------------

class _FileRangeHandler(_RangeHandler):
    data = b""


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs ffmpeg")
def test_ranged_sections_on_a_real_dash_mp4(tmp_path):
    import json
    import subprocess

    dash, audio = tmp_path / "dash.mp4", tmp_path / "audio.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25",
                    "-t", "20", "-c:v", "libx264", "-g", "50", "-keyint_min", "50",
                    "-sc_threshold", "0", "-an", "-movflags", "+dash+global_sidx", "-f", "mp4",
                    str(dash)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300",
                    "-t", "20", "-c:a", "aac", str(audio)], check=True)
    _FileRangeHandler.data = dash.read_bytes()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FileRangeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/v"
        info = {"formats": [{"format_id": "v", "ext": "mp4", "protocol": "https", "acodec": "none",
                             "vcodec": "avc1.64000d", "height": 180, "url": url}]}
        out = tmp_path / "out"
        out.mkdir()
        sections = af.ranged_sections(info, audio, [(3.0, 7.0), (12.5, 15.0)], out)
    finally:
        srv.shutdown()
    assert len(sections) == 2
    for sec, (a, b) in zip(sections, [(3.0, 7.0), (12.5, 15.0)]):
        assert sec["start"] <= a and sec["end"] >= b          # covers the asked range
        assert sec["end"] - sec["start"] <= (b - a) + 4.1     # at most one GOP extra each side
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                                "-of", "json", sec["path"]], capture_output=True, text=True)
        kinds = sorted(s["codec_type"] for s in json.loads(probe.stdout)["streams"])
        assert kinds == ["audio", "video"]

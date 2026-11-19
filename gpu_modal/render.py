"""Runs main.py on one video inside the image (standard library only).

Used by the Modal functions (gpu_modal/app.py); it also works in any
container built from the repository's Dockerfile:

    python -m gpu_modal.render --input talk.mp4 --output out/ --env OUTPUT_ASPECT=4:5
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(os.environ.get("OPENSHORTS_DIR", Path(__file__).resolve().parents[1]))
USAGE_FILE_NAME = "gemini_usage.jsonl"


class RenderFailed(Exception):
    """A verdict about the video (no clips found), not about the hardware:
    another GPU or the CPU would fail the same way."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def has_cuda():
    if not shutil.which("nvidia-smi"):
        return False
    try:
        return subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def device_name():
    """'NVIDIA L4', 'NVIDIA A10' ... or 'cpu'."""
    if not has_cuda():
        return "cpu"
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=20).stdout
        return out.strip().splitlines()[0]
    except (OSError, IndexError, subprocess.TimeoutExpired):
        return "GPU"


def run_env(settings, usage_file, cuda, base=None):
    """main.py's environment: GPU-friendly defaults, then the run's settings."""
    env = dict(os.environ if base is None else base)
    env.setdefault("AUTO_HOOK", "1")
    env.setdefault("CLIP_WORKERS", "3")
    env.setdefault("FFMPEG_ENCODER", "auto")  # NVENC when it works, x264 otherwise
    env.setdefault("WHISPER_MODEL", "large-v3-turbo")
    env["WHISPER_DEVICE"] = "cuda" if cuda else "cpu"
    env["WHISPER_COMPUTE"] = "float16" if cuda else "int8"
    env["GEMINI_USAGE_FILE"] = str(usage_file)
    env.pop("WATERMARK", None)
    env.update({k: str(v) for k, v in (settings or {}).items()})
    return env


def parse_clips(stdout, out_dir):
    """Clips announced with CLIP_READY, described by the *_metadata.json."""
    ready = {}
    for line in stdout.splitlines():
        m = re.match(r"CLIP_READY (\d+) (\S+)", line.strip())
        if m:
            ready[int(m.group(1))] = Path(out_dir) / m.group(2)
    metas = sorted(Path(out_dir).glob("*_metadata.json"))
    if not metas:
        raise RenderFailed("no_moments", "the pipeline wrote no metadata")
    shorts = json.loads(metas[0].read_text()).get("shorts") or []
    clips = []
    for i, short in enumerate(shorts):
        path = ready.get(i)
        if not path or not path.exists():
            continue
        score = short.get("predicted_score")
        clips.append({
            "index": i,
            "file": path.name,
            "title": (short.get("video_title_for_youtube_short") or f"Clip {i + 1}").strip(),
            "start": float(short.get("start", 0)),
            "end": float(short.get("end", 0)),
            "hook": str(short.get("viral_hook_text") or "").strip(),
            "score": int(score) if isinstance(score, (int, float)) else None,
            "reason": str(short.get("why") or "").strip(),
        })
    if not clips:
        raise RenderFailed("no_moments", "no clip was rendered")
    return clips, metas[0]


def usage_total(path):
    usd, calls = 0.0, 0
    try:
        for line in Path(path).read_text().splitlines():
            try:
                usd += float(json.loads(line).get("usd") or 0)
                calls += 1
            except (ValueError, AttributeError):
                continue
    except OSError:
        pass
    return {"usd": round(usd, 6), "calls": calls}


def render(source, out_dir, settings=None, clips_file=None, transcript_file=None):
    """Renders ``source`` (a file path, or a URL main.py downloads) into
    out_dir. Returns {"clips": [...], "metadata": name, "device", "seconds",
    "llm": {"usd", "calls"}}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    usage = out_dir / USAGE_FILE_NAME
    cuda = has_cuda()
    is_url = str(source).startswith(("http://", "https://"))
    cmd = [sys.executable, "main.py", "-u" if is_url else "-i", str(source), "-o", str(out_dir)]
    if clips_file:
        cmd += ["--clips", str(clips_file)]
    if transcript_file:
        cmd += ["--transcript", str(transcript_file)]
    started = time.monotonic()
    res = subprocess.run(cmd, cwd=REPO, env=run_env(settings, usage, cuda), capture_output=True,
                         text=True, timeout=3 * 3600)
    if res.returncode != 0 and not list(out_dir.glob("*_metadata.json")):
        tail = (res.stderr or res.stdout)[-1500:]
        if "Clip detection failed" in tail:
            raise RenderFailed("no_moments", "the model found no clips")
        raise RuntimeError(f"main.py failed ({res.returncode}): {tail}")
    clips, meta = parse_clips(res.stdout, out_dir)
    return {"clips": clips, "metadata": meta.name, "device": device_name(),
            "seconds": round(time.monotonic() - started, 1), "llm": usage_total(usage)}


def main(argv=None):
    p = argparse.ArgumentParser(description="Render one video with main.py and list the clips.")
    p.add_argument("--input", required=True, help="video file or URL")
    p.add_argument("--output", required=True)
    p.add_argument("--clips")
    p.add_argument("--transcript")
    p.add_argument("--env", action="append", default=[], help="KEY=VALUE for main.py")
    a = p.parse_args(argv)
    settings = dict(kv.split("=", 1) for kv in a.env)
    try:
        result = render(a.input, a.output, settings, a.clips, a.transcript)
    except RenderFailed as e:
        print(json.dumps({"failed": e.code, "detail": e.detail}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

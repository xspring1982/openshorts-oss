"""Render a video on the deployed Modal app, from your machine.

    from gpu_modal.client import render
    result = render("talk.mp4", "clips/", {"OUTPUT_ASPECT": "4:5"})

The GPU call has an overall time limit that includes Modal's queue. When it
runs out, or the call fails for a reason that is not about the video (no GPU
capacity, preemption, spend limit, a crash), the same render runs on the CPU
function instead, unless fallback="none".
"""
import io
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from gpu_modal import config
from gpu_modal.cost import CPU_CORES, CPU_FALLBACK_CORES, estimate_usd
from gpu_modal.render import RenderFailed

DEFAULT_GPU_TIMEOUT_S = 20 * 60


class GpuUnavailable(Exception):
    """The GPU call did not produce an answer (queue, crash, limits)."""


@dataclass
class RenderResult:
    clips: list            # dicts: index, file, title, start, end, hook, score, reason
    out_dir: Path
    backend: str           # "gpu" or "cpu"
    device: str
    wall_s: float
    billed_s: float = 0.0
    est_cost_usd: float = 0.0   # Modal compute only, from cost.py list prices
    llm_usd: float = 0.0        # from the Gemini meter
    fallback_reason: str = ""
    raw: dict = field(default_factory=dict)


def check_settings(settings):
    unknown = set(settings or {}) - set(config.RUN_ENV_KEYS)
    if unknown:
        raise ValueError(f"unknown setting(s): {', '.join(sorted(unknown))}; "
                         f"allowed: {', '.join(config.RUN_ENV_KEYS)}")
    return {k: str(v) for k, v in (settings or {}).items()}


def render(source, out_dir, settings=None, *, clips=None, transcript=None, fallback="cpu",
           gpu_timeout_s=DEFAULT_GPU_TIMEOUT_S, app_name=None):
    """Renders ``source`` (a local file, or a URL the container downloads)
    on Modal; the clips and the metadata JSON land in ``out_dir``."""
    import modal

    settings = check_settings(settings)
    if fallback not in ("cpu", "none"):
        raise ValueError("fallback must be 'cpu' or 'none'")
    out = Path(out_dir)
    is_url = str(source).startswith(("http://", "https://"))
    files = {}
    if not is_url:
        source = Path(source)
        # The clip files are named after the source, so keep its name.
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", source.name).lstrip(".") or "source.mp4"
        if safe in ("clips.json", "transcript.json"):
            safe = "source_" + safe
        files[safe] = source
    if clips:
        files["clips.json"] = Path(clips)
    if transcript:
        files["transcript.json"] = Path(transcript)
    for path in files.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    inputs = {"url": str(source) if is_url else None,
              "source": None if is_url else next(iter(files)),
              "clips": "clips.json" if clips else None,
              "transcript": "transcript.json" if transcript else None}
    app_name = app_name or config.APP_NAME
    key = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    vol = modal.Volume.from_name(config.VOLUME_NAME)
    started = time.monotonic()
    try:
        with vol.batch_upload(force=True) as batch:
            for name, path in files.items():
                batch.put_file(str(path), f"/io/{key}/{name}")
            if not files:  # a URL run still needs its folder
                batch.put_file(io.BytesIO(b""), f"/io/{key}/.keep")
        backend, reason = "gpu", ""
        try:
            result = _call(modal, app_name, "render_gpu", key, settings, inputs, gpu_timeout_s)
        except GpuUnavailable as e:
            if fallback == "none":
                raise
            backend, reason = "cpu", str(e)
            result = _call(modal, app_name, "render_cpu", key, settings, inputs,
                           config.CPU_FUNCTION_TIMEOUT_S + 600)
        if result.get("failed"):
            raise RenderFailed(result["failed"], result.get("detail", ""))
        out.mkdir(parents=True, exist_ok=True)
        for entry in vol.listdir(f"io/{key}/out"):
            with open(out / Path(entry.path).name, "wb") as f:
                for chunk in vol.read_file(entry.path):
                    f.write(chunk)
    finally:
        try:
            vol.remove_file(f"io/{key}", recursive=True)
        except Exception:  # noqa: BLE001 - nothing uploaded, or already gone
            pass
    billed = float(result.get("billed_s") or 0)
    cores = CPU_FALLBACK_CORES if backend == "cpu" else CPU_CORES
    return RenderResult(clips=result["clips"], out_dir=out, backend=backend,
                        device=result.get("device", ""), wall_s=round(time.monotonic() - started, 1),
                        billed_s=billed, est_cost_usd=estimate_usd(result.get("device"), billed, cores),
                        llm_usd=(result.get("llm") or {}).get("usd", 0.0), fallback_reason=reason,
                        raw=result)


def _call(modal, app_name, function, key, settings, inputs, timeout_s):
    try:
        fn = modal.Function.from_name(app_name, function)
        call = fn.spawn(key, settings, inputs)
    except Exception as e:  # noqa: BLE001 - app not deployed, auth, limits
        raise GpuUnavailable(f"{type(e).__name__}: {str(e)[:300]}") from None
    try:
        return call.get(timeout=timeout_s)
    except (TimeoutError, modal.exception.TimeoutError):
        try:
            call.cancel()
        except Exception:  # noqa: BLE001
            pass
        raise GpuUnavailable(f"no result after {timeout_s:.0f}s (queue or render)") from None
    except Exception as e:  # noqa: BLE001 - preemption, crash, spend limit
        raise GpuUnavailable(f"{type(e).__name__}: {str(e)[:300]}") from None

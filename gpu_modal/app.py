"""The Modal app: a GPU render function and a CPU fallback, one image.

Deploy once (settings from the environment, see config.py):

    modal secret create openshorts GEMINI_API_KEY=...   # plus GROQ_API_KEY, optional
    modal deploy -m gpu_modal.app

The image is the repository's own Dockerfile built with GPU=1 (CUDA torch,
onnxruntime-gpu, NVENC through ffmpeg, osrender), so a Modal run and a local
`docker compose` run execute the same code. Inputs and outputs pass through a
Modal Volume under io/<key>/; the client uploads, calls, downloads and
deletes the folder. Model downloads (Whisper) are cached in a second volume.
"""
import shutil
import time
from pathlib import Path

import modal

from gpu_modal import config

REPO = Path(__file__).resolve().parents[1]
IO = "/io"
CACHE = "/cache"

app = modal.App(config.APP_NAME)
image = (modal.Image.from_dockerfile(str(REPO / "Dockerfile"), context_dir=str(REPO),
                                     build_args={"GPU": "1"})
         .env({"OSGPU_SCALEDOWN_S": str(config.SCALEDOWN_S), "OPENSHORTS_DIR": "/app",
               "HF_HOME": f"{CACHE}/hf"})
         .add_local_python_source("gpu_modal"))
io_volume = modal.Volume.from_name(config.VOLUME_NAME, create_if_missing=True)
cache_volume = modal.Volume.from_name(config.CACHE_VOLUME_NAME, create_if_missing=True)
secrets = [modal.Secret.from_name(config.SECRET_NAME)]
volumes = {IO: io_volume, CACHE: cache_volume}

# Modal bills a container from start to stop. Each call reports its own busy
# seconds, plus (on the call that started the container) the time from
# container start to the call, plus the scale-down idle after it.
_CONTAINER_START = time.time()
_FIRST_CALL = [True]


def _billed(started_wall, busy):
    boot = started_wall - _CONTAINER_START if _FIRST_CALL[0] else 0.0
    _FIRST_CALL[0] = False
    return round(max(0.0, boot) + busy + config.SCALEDOWN_S, 1)


def _render(key, settings, inputs):
    from gpu_modal.render import RenderFailed, device_name, render

    started, started_wall = time.monotonic(), time.time()
    io_volume.reload()
    src = Path(IO) / "io" / key
    if not src.is_dir():
        # The client gave up and cleaned up before this queued call started.
        return {"failed": "cancelled", "device": device_name(), "busy_s": 0,
                "billed_s": _billed(started_wall, 0.0)}
    local = Path("/tmp/in") / key
    out = Path("/tmp/out") / key
    shutil.rmtree(local, ignore_errors=True)
    shutil.rmtree(out, ignore_errors=True)
    local.mkdir(parents=True)
    for f in src.iterdir():
        if f.is_file():
            shutil.copy(f, local / f.name)

    def opt(name):
        return str(local / inputs[name]) if inputs.get(name) else None

    source = inputs.get("url") or opt("source")
    try:
        result = render(source, out, settings, opt("clips"), opt("transcript"))
    except RenderFailed as e:
        busy = round(time.monotonic() - started, 1)
        return {"failed": e.code, "detail": e.detail, "device": device_name(), "busy_s": busy,
                "billed_s": _billed(started_wall, busy)}
    finally:
        shutil.rmtree(local, ignore_errors=True)
    dest = src / "out"
    dest.mkdir(exist_ok=True)
    keep = {c["file"] for c in result["clips"]} | {result["metadata"]}
    for name in keep:
        shutil.copy(out / name, dest / name)
    io_volume.commit()
    cache_volume.commit()
    shutil.rmtree(out, ignore_errors=True)
    busy = round(time.monotonic() - started, 1)
    result.update(busy_s=busy, billed_s=_billed(started_wall, busy))
    return result


@app.function(image=image, gpu=config.GPUS, cpu=4.0, memory=16 * 1024,
              timeout=config.GPU_FUNCTION_TIMEOUT_S, volumes=volumes, secrets=secrets,
              max_containers=config.MAX_CONTAINERS, min_containers=0,
              scaledown_window=config.SCALEDOWN_S)
def render_gpu(key: str, settings: dict, inputs: dict) -> dict:
    return _render(key, settings, inputs)


# The same image without a GPU: upstream falls back to x264, CPU torch and
# CPU Whisper by itself. Much slower; used when no GPU answers in time.
@app.function(image=image, cpu=8.0, memory=16 * 1024,
              timeout=config.CPU_FUNCTION_TIMEOUT_S, volumes=volumes, secrets=secrets,
              max_containers=config.MAX_CONTAINERS, min_containers=0,
              scaledown_window=config.SCALEDOWN_S)
def render_cpu(key: str, settings: dict, inputs: dict) -> dict:
    return _render(key, settings, inputs)

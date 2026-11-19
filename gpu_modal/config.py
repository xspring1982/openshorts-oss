"""Names and limits shared by the Modal app (at deploy time) and the client.

Everything comes from the environment, so a deploy and the client calling it
agree as long as they see the same values.
"""
import os


def _env(name, default):
    return (os.environ.get(name) or default).strip()


APP_NAME = _env("OSGPU_APP", "openshorts-gpu")
VOLUME_NAME = _env("OSGPU_VOLUME", "openshorts-gpu-io")
CACHE_VOLUME_NAME = _env("OSGPU_CACHE_VOLUME", "openshorts-gpu-models")
SECRET_NAME = _env("OSGPU_SECRET", "openshorts")
# GPU types in order of preference; Modal takes the first one with capacity.
GPUS = [g.strip() for g in _env("OSGPU_GPUS", "L4,A10").split(",") if g.strip()]
MAX_CONTAINERS = int(_env("OSGPU_MAX_CONTAINERS", "4"))
# Billed idle seconds after a call before the container stops.
SCALEDOWN_S = int(_env("OSGPU_SCALEDOWN_S", "10"))
GPU_FUNCTION_TIMEOUT_S = 30 * 60
CPU_FUNCTION_TIMEOUT_S = 2 * 60 * 60

# Settings a run may pass to main.py, by environment variable. Anything else
# a caller sends is refused (the client validates the same list).
RUN_ENV_KEYS = (
    "OUTPUT_ASPECT", "CAPTION_PRESET", "CAPTION_HIGHLIGHT", "CAPTION_FONT", "AUTO_HOOK",
    "AUTO_CAPTIONS", "GEMINI_MODEL", "CLIP_TARGET_MIN", "CLIP_TARGET_MAX", "TRANSCRIBE_BACKEND",
    "AUDIO_FIRST",
)

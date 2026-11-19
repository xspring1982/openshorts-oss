"""Output aspect ratio of the rendered clips (fork addition).

Upstream renders 9:16, 1:1 (``--format square``) or keeps 16:9 (``--format
horizontal``). Every reframe renderer (reframe_v2 on the CPU, gpu_render on
the card) already takes the ratio as an argument, so any ratio works; this
module picks it and sizes the captions and the hook to match.

Choose with ``OUTPUT_ASPECT`` (env) or ``main.py --aspect``: ``9:16``
(default), ``4:5``, ``1:1``, ``16:9``, or any ``W:H`` between 0.3 and 3.

Captions are sized in units of the frame HEIGHT (ASS PlayResY 288), so a wide
frame needs smaller type and fits longer lines; the hook's type follows its
box width. The presets below were tuned by eye on test renders. Each value
can be overridden:

  CAPTION_SCALE     factor on the caption font size
  CAPTION_CHARS     factor on the characters per caption line
  CAPTION_MARGIN_V  caption bottom margin in PlayResY units (9:16 keeps 43,
                    above the TikTok/Reels bottom UI)
  HOOK_WIDTH        hook box width as a share of the frame width
"""
import os
import sys

DEFAULT = "9:16"
PRESETS = {
    "9:16": {"CAPTION_SCALE": 1.0, "CAPTION_CHARS": 1.0, "CAPTION_MARGIN_V": 43, "HOOK_WIDTH": 0.9},
    "4:5": {"CAPTION_SCALE": 0.85, "CAPTION_CHARS": 1.3, "CAPTION_MARGIN_V": 30, "HOOK_WIDTH": 0.8},
    "1:1": {"CAPTION_SCALE": 0.8, "CAPTION_CHARS": 1.5, "CAPTION_MARGIN_V": 26, "HOOK_WIDTH": 0.7},
    "16:9": {"CAPTION_SCALE": 0.75, "CAPTION_CHARS": 1.9, "CAPTION_MARGIN_V": 20, "HOOK_WIDTH": 0.5},
}


def _argv_value(flag, argv=None):
    """The value after ``flag`` (or ``flag=value``) on the command line, so
    main.py --aspect works although the ratio is read at import time."""
    args = sys.argv if argv is None else argv
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


def parse(value):
    """'4:5' -> 0.8; None for anything that is not a sane W:H."""
    w, sep, h = str(value or "").strip().partition(":")
    if not sep:
        return None
    try:
        r = float(w) / float(h)
    except (ValueError, ZeroDivisionError):
        return None
    return r if 0.3 <= r <= 3.0 else None


def current(env=None, argv=None):
    """The chosen ratio as text, '9:16' when unset or invalid."""
    env = os.environ if env is None else env
    raw = env.get("OUTPUT_ASPECT") or _argv_value("--aspect", argv) or DEFAULT
    return raw.strip() if parse(raw) else DEFAULT


def ratio(env=None, argv=None):
    """Width / height of the output frame (9/16 by default)."""
    return parse(current(env, argv))


def _setting(name, env=None, argv=None):
    env = os.environ if env is None else env
    raw = env.get(name)
    if raw not in (None, ""):
        try:
            return float(raw)
        except ValueError:
            pass
    preset = PRESETS.get(current(env, argv))
    if preset is None:
        # A custom ratio: the settings of the nearest named one.
        r = ratio(env, argv)
        preset = PRESETS[min(PRESETS, key=lambda k: abs(parse(k) - r))]
    return float(preset[name])


def caption_scale(env=None, argv=None):
    return _setting("CAPTION_SCALE", env, argv)


def caption_chars(env=None, argv=None):
    return _setting("CAPTION_CHARS", env, argv)


def caption_margin_v(env=None, argv=None):
    return int(_setting("CAPTION_MARGIN_V", env, argv))


def hook_width(env=None, argv=None):
    return _setting("HOOK_WIDTH", env, argv)


def caption_line_units(env=None, argv=None):
    """Usable caption line width in ASS units: the PlayResX libass derives
    for this frame from PlayResY 288, minus the side margins."""
    return 288 * ratio(env, argv) - 20

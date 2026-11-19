"""python -m gpu_modal: render on the deployed Modal app.

  python -m gpu_modal render talk.mp4 -o clips/ [--aspect 4:5] [--caption-style hormozi]
  python -m gpu_modal render "https://www.youtube.com/watch?v=..." --audio-first
  python -m gpu_modal prices
  python -m gpu_modal doctor
"""
import argparse
import json
import sys
from pathlib import Path

from gpu_modal import config
from gpu_modal.cost import CPU_FALLBACK_CORES, GPU_USD_PER_S, usd_per_hour
from gpu_modal.render import RenderFailed


def cmd_render(a):
    settings = {}
    if a.aspect:
        settings["OUTPUT_ASPECT"] = a.aspect
    if a.caption_style:
        settings["CAPTION_PRESET"] = a.caption_style
    if a.highlight:
        settings["CAPTION_HIGHLIGHT"] = a.highlight
    if a.no_hook:
        settings["AUTO_HOOK"] = "0"
    if a.audio_first:
        settings["AUDIO_FIRST"] = "1"
    if a.max_clips:
        settings["CLIP_TARGET_MAX"] = str(a.max_clips)
    for kv in a.env:
        k, _, v = kv.partition("=")
        settings[k] = v
    out = Path(a.output or ("clips" if "://" in a.source else Path(a.source).stem + "-clips"))
    if a.dry_run:
        from gpu_modal.client import check_settings
        print(json.dumps({"app": config.APP_NAME, "gpus": config.GPUS, "source": a.source,
                          "out": str(out), "settings": check_settings(settings),
                          "fallback": a.fallback, "gpu_timeout_s": a.gpu_timeout}, indent=2))
        return 0
    from gpu_modal.client import render
    r = render(a.source, out, settings, clips=a.clips, transcript=a.transcript,
               fallback=a.fallback, gpu_timeout_s=a.gpu_timeout)
    for c in r.clips:
        score = f"{c['score']:>3}" if c.get("score") is not None else "  -"
        print(f"{score}  {c['start']:7.1f}-{c['end']:7.1f}s  {c['file']}  {c['title']}")
    print(f"{len(r.clips)} clip(s) in {r.out_dir}  backend={r.backend}  device={r.device}  "
          f"wall={r.wall_s:.0f}s  billed~{r.billed_s:.0f}s  modal~${r.est_cost_usd:.3f}  "
          f"llm~${r.llm_usd:.4f}")
    if r.fallback_reason:
        print(f"note: rendered on CPU because the GPU call failed: {r.fallback_reason}")
    return 0


def cmd_prices(_):
    print("Modal list prices (gpu_modal/cost.py, read 2026-11-18), per container hour:")
    for gpu in GPU_USD_PER_S:
        print(f"  {gpu:<5} + 4 cores + 16 GiB   ${usd_per_hour(gpu):.3f}/h")
    print(f"  CPU fallback, {CPU_FALLBACK_CORES:.0f} cores + 16 GiB   "
          f"${usd_per_hour(None, CPU_FALLBACK_CORES):.3f}/h")
    return 0


def cmd_doctor(_):
    try:
        import modal
    except ImportError:
        print("modal is not installed: pip install modal")
        return 1
    ok = True
    for fn in ("render_gpu", "render_cpu"):
        try:
            modal.Function.from_name(config.APP_NAME, fn).hydrate()
            print(f"ok   {config.APP_NAME}.{fn} is deployed")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"FAIL {config.APP_NAME}.{fn}: {type(e).__name__}: {str(e)[:200]}")
    if not ok:
        print("Deploy with: modal deploy -m gpu_modal.app")
    return 0 if ok else 1


def parser():
    p = argparse.ArgumentParser(prog="python -m gpu_modal")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("render", help="render one video (file or URL) on Modal")
    r.add_argument("source")
    r.add_argument("-o", "--output")
    r.add_argument("--aspect", help="9:16 (default), 4:5, 1:1, 16:9")
    r.add_argument("--caption-style", help="hormozi, pill, lime, oneword, cine, clean")
    r.add_argument("--highlight", help="active caption word colour, #RRGGBB")
    r.add_argument("--no-hook", action="store_true")
    r.add_argument("--audio-first", action="store_true", help="YouTube URLs: fetch only the picked ranges")
    r.add_argument("--max-clips", type=int)
    r.add_argument("--clips", help="reuse an earlier run's *_metadata.json")
    r.add_argument("--transcript", help="reuse a transcript JSON")
    r.add_argument("--env", action="append", default=[], help=f"KEY=VALUE, one of {', '.join(config.RUN_ENV_KEYS)}")
    r.add_argument("--fallback", choices=["cpu", "none"], default="cpu")
    r.add_argument("--gpu-timeout", type=float, default=20 * 60)
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_render)
    sub.add_parser("prices").set_defaults(func=cmd_prices)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    return p


def main(argv=None):
    a = parser().parse_args(argv)
    try:
        return a.func(a)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except RenderFailed as e:
        print(f"the video could not be clipped: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())

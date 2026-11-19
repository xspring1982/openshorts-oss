"""Per-call Gemini cost meter (fork addition).

Upstream prices only some calls (the pick stage's cost_analysis); the hook
grounding, layout picker, subtitle check and others call Gemini directly.
With GEMINI_USAGE_FILE set, main.py calls install() once, which wraps
google-genai's generate_content so every answered call appends one JSON line:

  {"model": ..., "in": prompt tokens, "out": answer tokens,
   "think": thinking tokens, "usd": cost at clip_selection's MODEL_PRICES,
   "priced": false when the model has no known price (then $0.50/$3.00 per
   million tokens is assumed)}

Sum the "usd" fields for the run's LLM spend. Without the variable nothing
is wrapped. Calling install() twice is a no-op.
"""
_INSTALLED = [False]


def install():
    if _INSTALLED[0]:
        return False
    import json as _json
    import os as _os
    _path = _os.environ.get("GEMINI_USAGE_FILE")
    if not _path:
        return False
    try:
        from google.genai import models as _models
    except Exception:
        return False

    def _record(model, response):
        try:
            usage = getattr(response, "usage_metadata", None)
            if not usage:
                return
            tin = int(getattr(usage, "prompt_token_count", 0) or 0)
            tout = int(getattr(usage, "candidates_token_count", 0) or 0)
            think = int(getattr(usage, "thoughts_token_count", 0) or 0)
            try:
                from clip_selection import lookup_model_prices
                prices = lookup_model_prices(model)
            except Exception:
                prices = None
            pin, pout = prices or (0.50, 3.00)
            usd = tin / 1e6 * pin + (tout + think) / 1e6 * pout
            with open(_path, "a") as f:
                f.write(_json.dumps({"model": str(model), "in": tin, "out": tout,
                                     "think": think, "usd": round(usd, 8),
                                     "priced": prices is not None}) + "\n")
        except Exception:
            pass

    def _model_of(args, kwargs):
        return kwargs.get("model") or (args[0] if args else "")

    _sync = _models.Models.generate_content

    def generate_content(self, *args, **kwargs):
        response = _sync(self, *args, **kwargs)
        _record(_model_of(args, kwargs), response)
        return response

    _models.Models.generate_content = generate_content
    _async = getattr(getattr(_models, "AsyncModels", None), "generate_content", None)
    if _async:
        async def agenerate_content(self, *args, **kwargs):
            response = await _async(self, *args, **kwargs)
            _record(_model_of(args, kwargs), response)
            return response

        _models.AsyncModels.generate_content = agenerate_content
    _INSTALLED[0] = True
    return True


def read_total(path):
    """{'usd': total, 'calls': n} of a meter file."""
    import json
    usd, calls = 0.0, 0
    try:
        with open(path) as f:
            for line in f:
                try:
                    usd += float(json.loads(line).get('usd') or 0)
                    calls += 1
                except (ValueError, AttributeError):
                    continue
    except OSError:
        pass
    return {'usd': round(usd, 6), 'calls': calls}

# Modal GPU backend (optional)

This backend renders clips on [Modal](https://modal.com) serverless GPUs. You
pay per second while a video renders and nothing while it is idle. It runs the
same `main.py` as a local install, in this repository's own Docker image built
with `GPU=1`: NVDEC decoding, CUDA TransNetV2 and YOLO, `osrender` reframing,
NVENC encoding and faster-whisper on CUDA.

When no GPU answers in time, or a GPU call fails for a reason that is not about
the video, the same render moves to a CPU container on the same image.

## Setup

You need a Modal account with a payment method (Modal does not run GPU
functions without one) and a Gemini API key.

```bash
pip install modal
modal token new                                   # browser login
modal secret create openshorts GEMINI_API_KEY=<key> [GROQ_API_KEY=<key>]
modal deploy -m gpu_modal.app                     # first build: about 20-30 min
python -m gpu_modal doctor
```

## Use

```bash
python -m gpu_modal render talk.mp4 -o clips/
python -m gpu_modal render talk.mp4 --aspect 4:5 --caption-style hormozi --max-clips 4
python -m gpu_modal render talk.mp4 -o square/ --aspect 1:1 --clips clips/talk_metadata.json
python -m gpu_modal render "https://www.youtube.com/watch?v=..." --audio-first
python -m gpu_modal prices
```

From Python:

```python
from gpu_modal.client import render
r = render("talk.mp4", "clips/", {"OUTPUT_ASPECT": "4:5", "CAPTION_PRESET": "pill"})
print(r.backend, r.device, r.est_cost_usd, [c["title"] for c in r.clips])
```

A run passes only the settings listed in `config.RUN_ENV_KEYS` to `main.py`.
YouTube often blocks datacenter IPs, Modal's included. If a URL fails there,
download the video yourself and render the file instead.

Deploy-time settings, from the environment: `OSGPU_GPUS` (default `L4,A10`),
`OSGPU_MAX_CONTAINERS` (default 4; your concurrency and cost ceiling),
`OSGPU_SCALEDOWN_S` (default 10; idle seconds before a container stops, which
are billed), and the names `OSGPU_APP`, `OSGPU_SECRET`, `OSGPU_VOLUME`,
`OSGPU_CACHE_VOLUME`. Use the same values for the client.

## Measured costs (November 2026)

The first row was measured with this repository's Modal backend; the others
were measured by the hosted service OpenShorts.io on the same pipeline, all
with an L4 or A10 container (4 physical cores, 16 GiB) and `CLIP_WORKERS=3`. Wall time
runs from the call to the result and includes the cold start. Cost is wall
time multiplied by the container's list price, an upper bound.

| Source | GPU | Wall | Clips | Cost | Per clip |
|---|---|---|---|---|---|
| **This repository**, first 3 min of the same source by URL, transcription on the GPU included, first call on a new image (2026-11-19) | A10 | 111 s (68 s billed) | 3 | $0.027 + $0.006 Gemini | $0.011 |
| 3 min (NASA *Black Hole 101*, public domain) | L4 | 100 s | 3 | $0.031 | $0.010 |
| 10 min, same source | L4 | 111 s | 6 | $0.034 | $0.006 |
| First call on a newly built image | L4 | 311–412 s | 3–6 | $0.10–0.13 | |
| One production hour, 15 jobs of 1–6 clips | mostly L4 | 35–141 s (median 54) | | **$0.022 per job** on average | |
| Early production jobs | mostly A10 | | | about $0.055 of GPU per job | |

- **No clean A10 benchmark yet.** An A10 container costs 27% more per second
  than an L4 one ($1.418/h vs $1.116/h). The runner prefers L4 and uses A10
  only when no L4 is free.
- Render time is about 60 s fixed plus about 10 s per clip, almost independent
  of the source length. On 4 shared vCPUs the same pipeline needed 45–60
  minutes for a 10-minute source.
- **The OpenShorts.io figures exclude transcription**, which ran on Groq's
  API there. The first row was measured with this repository and includes
  Whisper `large-v3-turbo` on the GPU, the download of the source and the
  first call on a freshly built image. Put `GROQ_API_KEY` in the
  secret and pass `--env TRANSCRIBE_BACKEND=groq` to transcribe on Groq first.
  Local faster-whisper remains the fallback.
- **GPU capacity is not guaranteed.** On one day, 3 of 8 L4 calls waited 8–30
  minutes in Modal's queue. That is why the client has the A10 second choice,
  an overall `--gpu-timeout` (default 20 minutes), and the CPU fallback.
- **The first call after an image build is slow**, because Modal loads the
  image lazily. Render a short video once after each deploy that rebuilt the
  image. Because the image copies the repository, any code change rebuilds
  its last layers.

Prices are Modal's list prices from 2026-11-18 (`cost.py`; check
modal.com/pricing). The cost line after a run is an estimate. Your Modal
dashboard shows what you are actually charged.

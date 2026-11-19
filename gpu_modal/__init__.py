"""Optional Modal backend: render clips on serverless GPUs (fork addition).

The same pipeline as a local run (main.py), in the repository's own image
built with GPU=1, on a Modal L4 (A10 when no L4 is free), billed per second
and scaled to zero when idle. When no GPU answers in time, or a GPU call
fails for a reason that is not about the video, the run moves to a CPU
container on the same image. See gpu_modal/README.md.

    pip install modal && modal token new
    modal secret create openshorts GEMINI_API_KEY=...
    modal deploy -m gpu_modal.app
    python -m gpu_modal render talk.mp4 -o clips/ --aspect 4:5
"""

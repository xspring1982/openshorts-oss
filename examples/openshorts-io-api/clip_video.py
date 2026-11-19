"""Clip a video with the hosted OpenShorts.io MCP endpoint (standard library only).

    export OPENSHORTS_API_KEY=os_...
    python clip_video.py "https://www.youtube.com/watch?v=..." [--aspect 4:5] [--style hormozi]
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
import urllib.request

ENDPOINT = os.environ.get("OPENSHORTS_MCP_URL", "https://openshorts.io/api/mcp")
_ids = itertools.count(1)


def call_tool(name: str, arguments: dict) -> dict:
    """One MCP tools/call; returns the tool's JSON result or raises."""
    body = {"jsonrpc": "2.0", "id": next(_ids), "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {os.environ['OPENSHORTS_API_KEY']}",
                 "Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=60) as res:
        reply = json.loads(res.read())
    if "error" in reply:
        raise RuntimeError(reply["error"].get("message"))
    result = reply["result"]
    text = result["content"][0]["text"]
    if result.get("isError"):
        raise RuntimeError(text)
    return json.loads(text)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--aspect", default="9:16", help="9:16, 4:5, 1:1 or 16:9")
    p.add_argument("--style", default="default", help="caption style, e.g. hormozi")
    a = p.parse_args()
    if not os.environ.get("OPENSHORTS_API_KEY"):
        sys.exit("set OPENSHORTS_API_KEY (create one at https://openshorts.io/settings/apikeys)")

    print("minutes:", call_tool("get_usage", {}))
    job = call_tool("clip_video", {"url": a.url, "aspect_ratio": a.aspect, "caption_style": a.style})
    print(f"job {job['job_id']} queued: {job['job_url']}")
    while True:
        time.sleep(30)
        job = call_tool("get_job", {"job_id": job["job_id"]})
        print(f"  {job['status']} {job.get('progress') or ''}")
        if job["status"] in ("done", "failed"):
            break
    if job["status"] == "failed":
        print("failed:", job.get("failure"))
        return 1
    for clip in job["clips"]:
        print(f"{clip['score']!s:>3}  {clip['title']}\n     {clip['video_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

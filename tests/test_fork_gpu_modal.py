"""gpu_modal (fork addition): no Modal account, GPU or network needed."""
import json

import pytest

from gpu_modal import config, cost, render
from gpu_modal.__main__ import main as cli


def test_run_env_defaults_and_settings(tmp_path):
    env = render.run_env({"OUTPUT_ASPECT": "4:5"}, tmp_path / "u.jsonl", cuda=True,
                         base={"WATERMARK": "1", "PATH": "/bin"})
    assert env["WHISPER_DEVICE"] == "cuda" and env["WHISPER_COMPUTE"] == "float16"
    assert env["WHISPER_MODEL"] == "large-v3-turbo" and env["AUTO_HOOK"] == "1"
    assert env["OUTPUT_ASPECT"] == "4:5" and "WATERMARK" not in env
    cpu = render.run_env({"AUTO_HOOK": "0"}, tmp_path / "u", cuda=False, base={})
    assert cpu["WHISPER_DEVICE"] == "cpu" and cpu["AUTO_HOOK"] == "0"


def test_parse_clips(tmp_path):
    (tmp_path / "talk_clip_1.mp4").write_bytes(b"x")
    (tmp_path / "talk_clip_3.mp4").write_bytes(b"x")
    (tmp_path / "talk_metadata.json").write_text(json.dumps({"shorts": [
        {"start": 1, "end": 20, "video_title_for_youtube_short": "One", "predicted_score": 91},
        {"start": 30, "end": 50},
        {"start": 60, "end": 80, "viral_hook_text": "Wait"}]}))
    stdout = "CLIP_READY 0 talk_clip_1.mp4\nCLIP_READY 2 talk_clip_3.mp4\n"
    clips, meta = render.parse_clips(stdout, tmp_path)
    assert [c["index"] for c in clips] == [0, 2] and clips[0]["score"] == 91
    assert clips[1]["hook"] == "Wait" and clips[1]["title"] == "Clip 3"
    assert meta.name == "talk_metadata.json"


def test_parse_clips_without_metadata_is_no_moments(tmp_path):
    with pytest.raises(render.RenderFailed) as e:
        render.parse_clips("", tmp_path)
    assert e.value.code == "no_moments"


def test_usage_total(tmp_path):
    f = tmp_path / "u.jsonl"
    f.write_text('{"usd": 0.004}\n{"usd": 0.001}\nbad\n')
    assert render.usage_total(f) == {"usd": 0.005, "calls": 2}


def test_check_settings_refuses_unknown_keys():
    from gpu_modal.client import check_settings
    assert check_settings({"OUTPUT_ASPECT": "1:1"}) == {"OUTPUT_ASPECT": "1:1"}
    with pytest.raises(ValueError, match="unknown"):
        check_settings({"GEMINI_API_KEY": "x"})


def test_cost_matches_the_published_container_price():
    assert cost.usd_per_hour("NVIDIA L4") == pytest.approx(1.116, abs=0.001)
    assert cost.usd_per_hour("NVIDIA A10G") == pytest.approx(1.418, abs=0.001)
    assert cost.estimate_usd("NVIDIA L4", 111) == pytest.approx(0.0344, abs=0.0005)
    assert cost.gpu_key("cpu") is None


def test_cli_dry_run(tmp_path, capsys):
    assert cli(["render", "talk.mp4", "--aspect", "4:5", "--caption-style", "hormozi",
                "--max-clips", "3", "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["settings"] == {"OUTPUT_ASPECT": "4:5", "CAPTION_PRESET": "hormozi",
                                "CLIP_TARGET_MAX": "3"}
    assert plan["out"] == "talk-clips" and plan["app"] == config.APP_NAME


def test_cli_refuses_unknown_env(capsys):
    assert cli(["render", "talk.mp4", "--env", "SECRET=1", "--dry-run"]) == 2


def test_modal_app_definition_loads():
    modal = pytest.importorskip("modal")
    from gpu_modal import app

    assert isinstance(app.render_gpu, modal.Function) and isinstance(app.render_cpu, modal.Function)
    app._FIRST_CALL[0] = True
    assert app._billed(app._CONTAINER_START + 5, 100) == pytest.approx(
        105 + config.SCALEDOWN_S, abs=0.2)

"""Fork additions: output aspect ratio, per-run caption look, CJK/RTL captions,
the Gemini usage meter. No ffmpeg, GPU or network needed."""
import json
import re
import types

import pytest

import output_aspect
import subtitles
import usage_meter


# ---- output_aspect ---------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("9:16", 9 / 16), ("4:5", 0.8), ("1:1", 1.0), ("16:9", 16 / 9), ("2:3", 2 / 3),
    ("", None), ("9x16", None), ("1:0", None), ("10:1", None), ("a:b", None),
])
def test_parse(value, expected):
    assert output_aspect.parse(value) == (pytest.approx(expected) if expected else None)


def test_env_wins_then_argv_then_default():
    assert output_aspect.current({}, ["main.py"]) == "9:16"
    assert output_aspect.current({}, ["main.py", "--aspect", "4:5"]) == "4:5"
    assert output_aspect.current({}, ["main.py", "--aspect=1:1"]) == "1:1"
    assert output_aspect.current({"OUTPUT_ASPECT": "16:9"}, ["main.py", "--aspect", "4:5"]) == "16:9"
    assert output_aspect.current({"OUTPUT_ASPECT": "nonsense"}, []) == "9:16"


def test_presets_and_overrides():
    env = {"OUTPUT_ASPECT": "16:9"}
    assert output_aspect.caption_scale(env) == 0.75
    assert output_aspect.caption_margin_v(env) == 20
    assert output_aspect.hook_width({"OUTPUT_ASPECT": "9:16"}) == 0.9
    assert output_aspect.caption_scale({**env, "CAPTION_SCALE": "0.6"}) == 0.6
    # A custom ratio takes the settings of the nearest named one.
    assert output_aspect.caption_chars({"OUTPUT_ASPECT": "3:4"}) == 1.3


def test_line_units_follow_the_frame_width():
    assert output_aspect.caption_line_units({}, []) == pytest.approx(288 * 9 / 16 - 20)
    assert output_aspect.caption_line_units({"OUTPUT_ASPECT": "1:1"}) == pytest.approx(268)


# ---- per-run caption look ----------------------------------------------------

def test_run_caption_style_preset_highlight_and_font():
    style = subtitles.run_caption_style("en", {"CAPTION_PRESET": "hormozi",
                                               "CAPTION_HIGHLIGHT": "#00E0FF"})
    assert style["font_name"] == subtitles.CAPTION_PRESETS["hormozi"]["font_name"]
    assert style["highlight_color"] == "#00E0FF"
    assert subtitles.run_caption_style("en", {}) == {}
    assert subtitles.run_caption_style("en", {"CAPTION_PRESET": "nope",
                                              "CAPTION_HIGHLIGHT": "red"}) == {}


@pytest.mark.parametrize("lang,font", [("ja", "Noto Sans CJK JP"), ("zh-CN", "Noto Sans CJK SC"),
                                       ("ar", "Noto Sans Arabic"), ("hi", "Noto Sans Devanagari")])
def test_script_font_from_the_transcript_language(lang, font):
    assert subtitles.run_caption_style(lang, {})["font_name"] == font


def test_caption_font_env_beats_the_language_font():
    assert subtitles.run_caption_style("ja", {"CAPTION_FONT": "My Font"})["font_name"] == "My Font"


def test_rtl_captions_are_not_uppercased():
    assert subtitles.run_caption_style("ar", {})["uppercase"] is False


def test_fit_caption_style_scales_for_wide_frames():
    base = dict(subtitles.AUTO_CAPTION_STYLE)
    assert subtitles.fit_caption_style(base, {"OUTPUT_ASPECT": "9:16"}) is base
    wide = subtitles.fit_caption_style(base, {"OUTPUT_ASPECT": "16:9"})
    assert wide["font_size"] == round(base["font_size"] * 0.75)
    assert wide["max_chars"] == round(base["max_chars"] * 1.9)
    one = subtitles.fit_caption_style({**base, "max_chars": 1}, {"OUTPUT_ASPECT": "16:9"})
    assert one["max_chars"] == 1  # the one-word preset stays one word


# ---- CJK / RTL ---------------------------------------------------------------

def test_join_words_has_no_spaces_between_cjk_words():
    assert subtitles._join_words(["今天", "天气", "很好"]) == "今天天气很好"
    assert subtitles._join_words(["hello", "world"]) == "hello world"
    assert subtitles._join_words(["OpenShorts", "很好"]) == "OpenShorts 很好"
    assert subtitles._join_words(["{\\c&H00D7FF&}今天{\\r}", "天气"]) == "{\\c&H00D7FF&}今天{\\r}天气"


def test_visual_len_counts_wide_glyphs_double():
    assert subtitles._visual_len("abc") == 3
    assert subtitles._visual_len("日本語") == 6
    assert subtitles._visual_len("한국") == 4


def test_generated_ass_joins_cjk_and_sets_bidi(tmp_path):
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, w in enumerate(["今天", "天气"])]
    transcript = {"language": "zh", "segments": [{"start": 0, "end": 1, "text": "", "words": words}]}
    out = tmp_path / "subs.ass"
    assert subtitles.generate_ass(transcript, 0, 1, str(out)) is True
    text = out.read_text(encoding="utf-8-sig")
    plain = re.sub(r"\{[^}]*\}", "", text)
    assert "今天天气" in plain and "今天 天气" not in plain
    style = [line for line in text.splitlines() if line.startswith("Style: Default")][0]
    assert style.endswith(",-1")


# ---- usage meter -------------------------------------------------------------

def test_meter_records_each_call(tmp_path, monkeypatch):
    genai_models = pytest.importorskip("google.genai.models")
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("GEMINI_USAGE_FILE", str(log))
    usage = types.SimpleNamespace(prompt_token_count=1000, candidates_token_count=200,
                                  thoughts_token_count=0)
    monkeypatch.setattr(genai_models.Models, "generate_content",
                        lambda self, *a, **k: types.SimpleNamespace(usage_metadata=usage))
    monkeypatch.setattr(usage_meter, "_INSTALLED", [False])
    assert usage_meter.install() is True
    assert usage_meter.install() is False  # second call is a no-op
    genai_models.Models.generate_content(object(), model="gemini-test", contents="x")
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert rows[0]["model"] == "gemini-test" and rows[0]["in"] == 1000 and rows[0]["usd"] > 0
    assert usage_meter.read_total(log)["calls"] == 1


def test_meter_is_off_without_the_variable(monkeypatch):
    monkeypatch.delenv("GEMINI_USAGE_FILE", raising=False)
    monkeypatch.setattr(usage_meter, "_INSTALLED", [False])
    assert usage_meter.install() is False

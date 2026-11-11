import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from boti.ai import LocalAI, transcribe
from boti.editing import caption_lines, caption_phrases, refine_boundaries
from boti.media import captions_ass, loudness_filter
from boti.pipeline import relative_segments


def word(start, end, text):
    return {"start": start, "end": end, "word": text}


def test_cuts_align_with_nearby_sentences_and_preserve_the_payoff():
    segments = [
        {
            "start": 1,
            "end": 24,
            "text": "A full story.",
            "words": [
                word(1, 2, "The"),
                word(2, 3, "setup."),
                word(4, 5, "Then"),
                word(23, 24, "payoff!"),
            ],
        }
    ]
    clip = refine_boundaries({"start": 1.5, "end": 23.5}, segments, 40)
    assert clip["start"] == pytest.approx(0.92)
    assert clip["end"] == pytest.approx(24.12)
    assert 20 <= clip["end"] - clip["start"] <= 60


def test_visual_clip_without_speech_keeps_its_boundaries():
    clip = {"start": 5, "end": 35, "score": 90}
    assert refine_boundaries(clip, [], 50) == clip
    assert refine_boundaries(clip, [], 30) is None


def test_impossible_boundary_does_not_cut_a_word_to_fit_sixty_seconds():
    segments = [{"words": [word(0, 0.2, "Start"), word(59, 62, "unfinished")]}]
    assert refine_boundaries({"start": 0, "end": 60}, segments, 65) is None


def test_captions_break_on_pause_punctuation_and_limit_lines():
    words = [
        word(0, 0.4, "Hello,"),
        word(0.4, 0.8, "world!"),
        word(0.8, 1.1, "New"),
        word(2, 2.4, "sentence"),
        word(2.4, 2.8, "with-a-long-description"),
        word(2.8, 3.2, "another-long-description"),
        word(3.2, 3.6, "finished."),
    ]
    groups = caption_phrases([{"words": words}], 3.4)
    assert [[w["word"] for w in group] for group in groups[:2]] == [["Hello,", "world!"], ["New"]]
    assert all(len(caption_lines(group)) <= 2 for group in groups)
    assert groups[-1][-1]["end"] == 3.4
    assert sum(len(group) for group in groups) == len(words)


def test_highlight_uses_real_word_times_and_never_displays_across_silence(tmp_path):
    path = tmp_path / "captions.ass"
    captions_ass(
        [{"words": [word(0.2, 0.6, r"{\pos(0,0)}Hi"), word(2, 2.4, "world.")]}], path, 20, "highlight"
    )
    lines = [
        line for line in path.read_text(encoding="utf-8-sig").splitlines() if line.startswith("Dialogue:")
    ]
    assert len(lines) == 2
    assert ",0:00:00.20,0:00:00.60," in lines[0]
    assert ",0:00:02.00,0:00:02.40," in lines[1]
    assert r"{\c&H66E8FF&}" in lines[0]
    assert r"\pos" not in lines[0]


def test_fallback_text_is_readable_without_fake_word_highlighting(tmp_path):
    path = tmp_path / "captions.ass"
    captions_ass([{"start": 0, "end": 20, "text": "A whole sentence. " * 8}], path, 20, "highlight")
    lines = [
        line for line in path.read_text(encoding="utf-8-sig").splitlines() if line.startswith("Dialogue:")
    ]
    assert len(lines) > 1
    assert all(r"\c" not in line for line in lines)
    assert all(line.count(r"\N") <= 1 for line in lines)


def test_partial_segment_captions_do_not_include_speech_outside_the_clip():
    result = relative_segments(
        [
            {
                "start": 0,
                "end": 30,
                "text": "before inside after",
                "words": [word(0, 1, "before"), word(10, 11, "inside"), word(29, 30, "after")],
            }
        ],
        5,
        25,
    )
    assert result[0]["text"] == "inside"
    result = relative_segments([{"start": 0, "end": 30, "text": "no trustworthy word timing"}], 5, 25)
    assert result[0]["text"] == ""


def test_ai_rechecks_overlapping_candidates_before_dedup(monkeypatch, tmp_path):
    monkeypatch.setattr("boti.ai.storyboard", lambda _, dest, *args: dest.write_bytes(b"fixture"))
    monkeypatch.setattr("boti.ai.energy_peaks", lambda _: [])
    monkeypatch.setattr("boti.ai.scene_changes", lambda _: [])
    checked = []

    def transport(request):
        if request.url.path.endswith("generate"):
            return httpx.Response(200, json={})
        body = json.loads(request.content)
        if "candidates" in body["format"]["properties"]:
            result = {
                "candidates": [
                    {"start": 0, "end": 30, "score": 80, "title": "weak"},
                    {"start": 2, "end": 32, "score": 95, "title": "strong"},
                ]
            }
        else:
            clip = json.loads(body["messages"][1]["content"].split("\n", 1)[1])["clip"]
            checked.append(clip["title"])
            result = {
                "approved": True,
                "score": 92 if clip["title"] == "strong" else 80,
                "reason": "Complete payoff",
            }
        return httpx.Response(200, json={"message": {"content": json.dumps(result)}})

    result = LocalAI(client=httpx.Client(transport=httpx.MockTransport(transport))).analyze(
        Path("audio"), Path("proxy"), {"segments": []}, 60, tmp_path
    )
    assert checked == ["strong", "weak"]
    assert [c["title"] for c in result["candidates"]] == ["strong"]
    assert result["candidates"][0]["verification_reason"] == "Complete payoff"


def test_loudness_silence_is_not_passed_as_infinity_to_ffmpeg(monkeypatch):
    result = {
        "input_i": "-inf",
        "input_tp": "-inf",
        "input_lra": "0",
        "input_thresh": "-70",
        "target_offset": "inf",
    }
    monkeypatch.setattr(
        "boti.media.run", lambda *args, **kwargs: SimpleNamespace(stderr=json.dumps(result).encode())
    )
    audio_filter, report = loudness_filter(Path("silence.wav"), 20)
    assert audio_filter == "anull" and report["mode"] == "silent"


def test_whisper_receives_source_language_and_vocabulary(monkeypatch):
    import sys

    captured = {}

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, path, **kwargs):
            captured.update(kwargs)
            return iter([]), SimpleNamespace(language="sq")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    result = transcribe(Path("unused.wav"), language="sq", vocabulary="Prishtina, emri i lojës")
    assert captured["language"] == "sq"
    assert captured["hotwords"] == "Prishtina, emri i lojës"
    assert captured["word_timestamps"] is True and result["language"] == "sq"

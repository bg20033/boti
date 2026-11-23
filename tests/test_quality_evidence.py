import json
from types import SimpleNamespace

from boti.quality import check_rendered


def test_quality_uses_output_relative_caption_phrases(monkeypatch, tmp_path):
    monkeypatch.setattr("boti.media.validate_output", lambda *args: {"duration": 12})
    monkeypatch.setattr(
        "boti.media.loudness_filter",
        lambda *args: ("anull", {"input_lufs": None, "input_peak_db": None}),
    )
    monkeypatch.setattr("boti.media.storyboard", lambda *args, **kwargs: None)
    captured = {}

    def chat(system, prompt, schema, images):
        captured.update(json.loads(prompt.split("\n", 1)[1]))
        return {"approved": True, "score": 90, "reasons": ["Readable captions"], "repair": "none"}

    ai = SimpleNamespace(
        chat=chat, client=SimpleNamespace(post=lambda *args, **kwargs: None), url="", model="test"
    )
    words = [
        {"start": 0, "end": 1, "word": "first"},
        {"start": 1, "end": 2, "word": "phrase"},
        {"start": 8, "end": 9, "word": "last"},
        {"start": 9, "end": 10, "word": "phrase"},
    ]
    payload = {
        "metadata": {
            "start": 100,
            "end": 112,
            "source_duration": 300,
            "source_context": [{"start": 200, "end": 210, "text": "Later unrelated speech"}],
            "segments": [{"start": 0, "end": 10, "text": "", "words": words}],
        },
        "rules": {"clip": {"min_seconds": 5, "max_seconds": 20}},
    }
    check_rendered(ai, tmp_path / "output.mp4", payload, tmp_path)
    frames = captured["sampled_frame_captions"]
    assert [frame["seconds"] for frame in frames] == [1, 3, 5, 7, 9, 11]
    assert [frame["expected_caption"] for frame in frames] == ["first phrase", "", "", "", "last phrase", ""]
    assert "source_context" not in captured["clip"]

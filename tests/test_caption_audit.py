from types import SimpleNamespace

import pytest

from boti.caption_audit import inspect_captions
from boti.quality import check_rendered


@pytest.mark.parametrize(
    "observations,passed",
    [
        ([{"index": 0, "text": "Hello world!", "readable": True}], True),
        ([{"index": 0, "text": "Hello", "readable": True}], False),
        ([{"index": 0, "text": "Hello world", "readable": False}], False),
        (
            [
                {"index": 0, "text": "Hello world", "readable": True},
                {"index": 0, "text": "Hello world", "readable": True},
            ],
            False,
        ),
    ],
)
def test_blind_reader_must_match_every_word_without_receiving_expected_text(tmp_path, observations, passed):
    def chat(system, prompt, schema, images):
        assert "Hello" not in system + prompt
        return {"observations": observations}

    result = inspect_captions(
        SimpleNamespace(chat=chat),
        tmp_path / "image.jpg",
        [{"seconds": 1, "expected_caption": "hello world"}],
    )
    assert result["passed"] is passed


@pytest.mark.parametrize(
    "ocr_ok,editorial_ok,score", [(True, True, 90), (False, True, 90), (True, False, 30), (True, True, 40)]
)
def test_caption_dispute_requires_exact_ocr_and_a_fresh_editorial_review(
    monkeypatch, tmp_path, ocr_ok, editorial_ok, score
):
    segments = [
        {
            "start": 0.2,
            "end": 11,
            "text": "Hello. Goodbye.",
            "words": [
                {"start": 0.2, "end": 1, "word": "Hello."},
                {"start": 10, "end": 11, "word": "Goodbye."},
            ],
        }
    ]
    monkeypatch.setattr("boti.media.validate_output", lambda *a: {"duration": 12})
    monkeypatch.setattr("boti.media.loudness_filter", lambda *a: ("anull", {"input_lufs": None}))
    monkeypatch.setattr("boti.media.storyboard", lambda *a, **k: None)
    monkeypatch.setattr("boti.ai.transcribe", lambda *a, **k: {"segments": segments})
    calls = []

    def chat(system, prompt, schema, images):
        calls.append(schema)
        if "observations" in schema["properties"]:
            return {
                "observations": [
                    {"index": 0, "text": "Hello." if ocr_ok else "Wrong.", "readable": True},
                    {"index": 1, "text": "Goodbye.", "readable": True},
                ]
            }
        if len(calls) == 1:
            return {"approved": False, "score": 0, "reasons": ["Wrong caption claim"], "repair": "captions"}
        return {
            "approved": editorial_ok,
            "score": score,
            "reasons": ["Fresh editorial decision"],
            "repair": "none",
        }

    ai = SimpleNamespace(
        chat=chat, client=SimpleNamespace(post=lambda *a, **k: None), url="", model="fixture"
    )
    config = SimpleNamespace(whisper_model="medium", whisper_device="cpu")
    result = check_rendered(
        ai,
        tmp_path / "clip.mp4",
        {
            "metadata": {"start": 0, "end": 12, "segments": segments},
            "rules": {"clip": {"min_seconds": 5, "max_seconds": 20}},
        },
        tmp_path,
        config,
    )
    audit = result["technical"]["caption_recheck"]
    assert audit["initial_verdict"]["approved"] is False
    assert audit["inspection"]["passed"] is ocr_ok
    assert result["technical_passed"]
    assert result["verdict"]["approved"] is (ocr_ok and editorial_ok)
    assert len(calls) == (3 if ocr_ok else 2)
    if ocr_ok:
        assert result["verdict"]["score"] == score  # A low score remains below the existing publication gate.

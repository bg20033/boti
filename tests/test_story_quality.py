import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from boti.db import encode, uid
from boti.editing import boundary_evidence, refine_boundaries
from boti.pipeline import Pipeline
from boti.quality import check_rendered


def words(*items):
    return [{"start": a, "end": b, "word": text} for a, b, text in items]


def test_trailing_ellipsis_is_trimmed_to_a_complete_ending():
    segments = [{"words": words((0.2, 1, "Setup."), (19, 20, "Payoff!"), (20.1, 21, "And..."))}]
    aligned = refine_boundaries({"start": 0, "end": 21.12}, segments, 30)
    assert aligned["end"] == pytest.approx(20.1)
    assert boundary_evidence(segments, aligned["start"], aligned["end"])["passed"]
    assert not boundary_evidence(segments, 0, 21.12)["passed"]


def test_complete_ending_can_be_found_more_than_two_seconds_away():
    segments = [{"words": words((0.2, 1, "Setup."), (25, 28.5, "payoff!"))}]
    aligned = refine_boundaries({"start": 0, "end": 25}, segments, 40)
    assert aligned["end"] == pytest.approx(28.62)


def test_no_padding_or_fake_completion_when_recording_ends_mid_sentence():
    segments = [{"words": words((0, 1, "Setup."), (19, 20, "We"), (20, 21, "have..."))}]
    assert refine_boundaries({"start": 0, "end": 21}, segments, 21) is None
    assert boundary_evidence([], 0, 30)["passed"]  # Silent visual action remains eligible.


def test_word_gap_is_not_automatically_a_sentence_boundary():
    segments = [{"words": words((1, 1.4, "We"), (1.5, 2, "have"), (2.1, 3, "won!"))}]
    assert not boundary_evidence(segments, 0, 2.05)["passed"]
    assert not boundary_evidence(segments, 1.45, 3.12)["passed"]
    assert boundary_evidence(segments, 0.92, 3.12)["passed"]


def test_main_rechecks_legacy_worker_cuts_before_planning(store):
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute("UPDATE jobs SET stage='analyze' WHERE id=?", (job,))
    segments = [
        {
            "words": words((0, 1, "Setup."), (19, 20, "We"), (20, 21, "have...")),
            "start": 0,
            "end": 21,
            "text": "Setup. We have...",
        }
    ]
    Pipeline(store).plan_clips(
        store.one("SELECT * FROM jobs WHERE id=?", (job,)),
        [
            {
                "payload": encode({"offset": 0, "duration": 21, "transcript": {"segments": segments}}),
                "result": encode(
                    {"candidates": [{"start": 0, "end": 21, "score": 99, "title": "A false complete story"}]}
                ),
            }
        ],
    )
    assert store.one("SELECT count(*) n FROM clips WHERE job_id=?", (job,))["n"] == 0


def test_existing_clip_refinement_is_versioned_and_rebuilds_relative_captions(client, store, admin_headers):
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"], review_mode="automatic")
    directory = store.config.root / "media" / job
    directory.mkdir()
    source = directory / "source.mp4"
    source.write_bytes(b"retained source")
    store.execute(
        "UPDATE jobs SET metadata=? WHERE id=?",
        (encode({"source": str(source.relative_to(store.config.root))}), job),
    )
    clip = uid()
    context = [
        {
            "start": 0.2,
            "end": 26,
            "text": "Setup. Payoff!",
            "words": words((0.2, 1, "Setup."), (23, 26, "Payoff!")),
        }
    ]
    metadata = {
        "start": 0,
        "end": 24.5,
        "score": 90,
        "title": "Title",
        "source_duration": 40,
        "source_context": context,
        "top_title_override": "My title",
    }
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,created,updated) VALUES (?,?,'pc-1-g1',0,24.5,90,?,0,0)",
        (clip, job, encode(metadata)),
    )
    url = f"/api/clips/{clip}/refine"
    assert client.post(url, json={"expected_version": 1}).status_code == 401
    response = client.post(url, headers=admin_headers, json={"expected_version": 1})
    assert response.status_code == 200 and response.json() == {"version": 2, "changed": True}
    current = store.one("SELECT * FROM clips WHERE id=?", (clip,))
    result = json.loads(current["metadata"])
    assert current["status"] == "preparing" and current["end"] == pytest.approx(26.12)
    assert result["segments"][0]["words"][-1]["end"] == pytest.approx(26 - current["start"])
    assert result["top_title_override"] == "My title" and result["editorial_evidence"]["passed"]
    old = store.one("SELECT metadata FROM clip_versions WHERE clip_id=? AND version=1", (clip,))
    assert json.loads(old["metadata"]) == metadata
    assert client.post(url, headers=admin_headers, json={"expected_version": 1}).status_code == 409
    assert client.post(url, headers=admin_headers, json={"expected_version": 2}).json()["changed"] is False


def test_final_quality_sees_closeups_and_cannot_approve_a_trailing_sentence(monkeypatch, tmp_path):
    monkeypatch.setattr("boti.media.validate_output", lambda *args: {"duration": 21})
    monkeypatch.setattr("boti.media.loudness_filter", lambda *args: ("anull", {"input_lufs": None}))
    boards, captured = [], {}
    monkeypatch.setattr("boti.media.storyboard", lambda *args, **kwargs: boards.append(kwargs))

    def chat(system, prompt, schema, images):
        captured.update(json.loads(prompt.split("\n", 1)[1]))
        assert len(images) == 2
        return {"approved": True, "score": 99, "reasons": ["Model missed the cut"], "repair": "none"}

    ai = SimpleNamespace(chat=chat, client=SimpleNamespace(post=lambda *a, **k: None), url="", model="test")
    timeline = words((0.2, 1, "Setup."), (19, 20, "We"), (20, 21, "have..."))
    payload = {
        "metadata": {
            "start": 0,
            "end": 21,
            "segments": [{"start": 0.2, "end": 21, "text": "Setup. We have...", "words": timeline}],
        },
        "rules": {},
        "editing": {"caption_position": "middle"},
    }
    result = check_rendered(ai, Path("fixture.mp4"), payload, tmp_path)
    assert result["technical_passed"] is False
    assert result["verdict"]["repair"] == "boundaries"
    assert result["technical"]["story_boundaries"]["passed"] is False
    assert boards[1]["caption_position"] == "middle"
    assert boards[1]["timestamps"] == [s["seconds"] for s in captured["caption_closeups"]]
    assert captured["caption_closeups"][0]["expected_caption"] == "Setup."
    assert captured["caption_closeups"][-1]["expected_caption"] == "We have..."

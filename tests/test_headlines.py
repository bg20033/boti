import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from boti.ai import LocalAI
from boti.db import encode
from boti.headlines import Headline
from boti.pipeline import Pipeline


def headline(**changes):
    return {
        "title": "He challenged the champion and instantly regretted it",
        "caption": "His opponent won.",
        "evidence": "The challenger loses the final round in the clip.",
        "third_person": True,
        "grounded": True,
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"grounded": False},
        {"third_person": False},
        {"evidence": "  "},
        {"title": " "},
        {"title": "x" * 91},
        {"title": "You won't believe his reaction"},
        {"title": "My opponent made a mistake"},
    ],
)
def test_invalid_or_unsupported_headlines_are_rejected(changes):
    with pytest.raises(ValidationError):
        Headline.model_validate(headline(**changes))


@pytest.mark.parametrize("unsupported", [False, True])
def test_verified_hook_replaces_first_person_proposal_before_rendering(
    monkeypatch, tmp_path, store, unsupported
):
    monkeypatch.setattr("boti.ai.storyboard", lambda _, dest, *a: dest.write_bytes(b"fixture"))
    monkeypatch.setattr("boti.ai.energy_peaks", lambda _: [])
    monkeypatch.setattr("boti.ai.scene_changes", lambda _: [])
    calls = []

    def transport(request):
        if request.url.path.endswith("generate"):
            return httpx.Response(200, json={})
        body = json.loads(request.content)
        calls.append(body)
        if "candidates" in body["format"]["properties"]:
            result = {"candidates": [{"start": 0, "end": 30, "score": 94, "title": "I beat you!"}]}
        else:
            result = {
                "approved": True,
                "score": 90,
                "reason": "Complete visual payoff",
                "headline": headline(grounded=not unsupported),
            }
        return httpx.Response(200, json={"message": {"content": json.dumps(result)}})

    result = LocalAI(client=httpx.Client(transport=httpx.MockTransport(transport))).analyze(
        Path("audio"), Path("proxy"), {"segments": []}, 60, tmp_path
    )
    assert all("THIRD PERSON" in call["messages"][0]["content"] for call in calls)
    assert "INSIDE this cut" in calls[1]["messages"][1]["content"]
    if unsupported:
        assert result["candidates"] == []
        return
    candidate = result["candidates"][0]
    assert candidate["title"] == headline()["title"] and candidate["score"] == 90
    assert candidate["caption"] == headline()["caption"]
    assert candidate["headline_evidence"] == headline()["evidence"]
    assert candidate["headline_style"] == "third_person_curiosity"
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    Pipeline(store).plan_clips(
        store.one("SELECT * FROM jobs WHERE id=?", (job,)),
        [
            {
                "payload": encode({"offset": 0, "duration": 60, "transcript": {"segments": []}}),
                "result": encode(result),
            }
        ],
    )
    saved = json.loads(store.one("SELECT metadata FROM clips WHERE job_id=?", (job,))["metadata"])
    assert saved["headline_evidence"] == candidate["headline_evidence"]
    assert saved["title"] == candidate["title"]

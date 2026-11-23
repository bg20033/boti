import json
import time

import pytest

from boti.db import uid


def add_clip(store, state, title, *, video=False):
    job_id, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    identity = uid()
    asset = None
    if video:
        path = store.config.root / "media" / f"{identity}.mp4"
        path.write_bytes(b"existing rendered video")
        asset = store.add_asset(job_id, path)["id"]
    metadata = {"title": title, "start": 0, "end": 30, "caption": "Clip caption", "segments": []}
    store.execute(
        """INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,status,asset_id,created,updated)
        VALUES (?,?,'pc-1-g1',0,30,85,?,?,?,?,?)""",
        (identity, job_id, json.dumps(metadata), state, asset, time.time(), time.time()),
    )
    return identity


@pytest.mark.parametrize("state", ["checking", "skipped", "ready"])
def test_library_exposes_existing_video_for_every_review_state(client, store, admin_headers, state):
    identity = add_clip(store, state, "A rendered moment", video=True)
    assert client.get("/api/clips").status_code == 401
    result = client.get("/api/clips", headers=admin_headers).json()
    assert result["total"] == 1
    clip = result["clips"][0]
    assert clip["status"] == state and clip["video_url"] == f"/api/clips/{identity}/video"
    assert client.get(clip["video_url"]).status_code == 401
    assert client.get(clip["video_url"], headers=admin_headers).content == b"existing rendered video"
    assert store.one("SELECT status FROM clips WHERE id=?", (identity,))["status"] == state
    path = store.config.root / "media" / f"{identity}.mp4"
    path.unlink()
    assert client.get("/api/clips", headers=admin_headers).json()["clips"][0]["video_url"] is None


def test_filters_search_and_pagination_share_the_same_total(client, store, admin_headers):
    add_clip(store, "checking", "Great opening")
    add_clip(store, "correcting", "Great payoff")
    add_clip(store, "skipped", "Great ending")
    add_clip(store, "ready", "Different moment")
    result = client.get("/api/clips?state=review&q=Great&limit=1", headers=admin_headers).json()
    assert result["total"] == 2 and len(result["clips"]) == 1
    second = client.get("/api/clips?state=review&q=Great&limit=1&offset=1", headers=admin_headers).json()
    assert second["total"] == 2 and second["clips"][0]["id"] != result["clips"][0]["id"]
    assert client.get("/api/clips?state=ready", headers=admin_headers).json()["total"] == 1
    assert client.get("/api/clips?state=skipped", headers=admin_headers).json()["total"] == 1
    assert client.get("/api/clips?state=processing", headers=admin_headers).json()["total"] == 0


def test_search_treats_wildcards_as_text_and_rejects_invalid_filters(client, store, admin_headers):
    add_clip(store, "skipped", "100%_interesting")
    add_clip(store, "skipped", "100x interesting")
    result = client.get("/api/clips", params={"q": "%_"}, headers=admin_headers).json()
    assert result["total"] == 1 and result["clips"][0]["metadata"]["title"] == "100%_interesting"
    assert client.get("/api/clips", params={"q": "' OR 1=1 --"}, headers=admin_headers).json()["total"] == 0
    assert client.get("/api/clips?state=unknown", headers=admin_headers).status_code == 422
    assert client.get("/api/clips", params={"q": "x" * 201}, headers=admin_headers).status_code == 422

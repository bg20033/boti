import time

import pytest

from boti.db import encode, uid
from boti.feeds import FeedWatcher
from boti.pipeline import Pipeline


@pytest.mark.parametrize(
    "mode,provider,age,campaign,removed",
    [
        ("automatic", "twitch", 120, False, True),
        ("automatic", "twitch", 30, False, False),
        ("manual", "twitch", 120, False, False),
        ("automatic", "twitch", 120, True, False),
        ("automatic", "file", 120, False, False),
    ],
)
def test_pressure_cleanup_only_expires_completed_automatic_vod_cache(
    store, mode, provider, age, campaign, removed
):
    job, _ = store.create_job(
        "https://twitch.tv/videos/123" if provider == "twitch" else "file:input.mp4",
        "123",
        ["pc-1-g1"],
        provider=provider,
        review_mode=mode,
    )
    folder = store.config.root / "media" / job
    folder.mkdir()
    source = folder / "source.mp4"
    source.write_bytes(b"raw source cache")
    store.add_asset(job, source)
    final = folder / "final.mp4"
    final.write_bytes(b"finished video")
    asset = store.add_asset(job, final)
    now = time.time()
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,status,asset_id,created,updated) VALUES(?,?,'pc-1-g1',0,30,90,'{}','ready',?,?,?)",
        (uid(), job, asset["id"], now, now),
    )
    store.execute("UPDATE jobs SET status='complete',completed=? WHERE id=?", (now - age * 60, job))
    if campaign:
        store.execute("INSERT INTO campaigns(id,data,created,updated) VALUES('protected','{}',0,0)")
        store.execute("INSERT INTO job_campaigns VALUES(?,'protected',1,'{}')", (job,))
    waiting, _ = store.create_job("https://twitch.tv/videos/456", "456", ["pc-1-g1"])
    store.execute("UPDATE jobs SET metadata=? WHERE id=?", (encode({"reserve_bytes": 10**15}), waiting))
    Pipeline(store).cleanup()
    assert source.exists() is not removed
    assert final.read_bytes() == b"finished video"


def test_disk_blocked_vod_does_not_consume_feed_slot_for_smaller_sources(client, store, admin_headers):
    waiting, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute("UPDATE jobs SET metadata=? WHERE id=?", (encode({"reserve_bytes": 10**15}), waiting))
    body = {"channel": "xqc", "group_ids": ["pc-1-g1"], "pending_limit": 1}
    assert client.post("/api/feeds", headers=admin_headers, json=body).status_code == 200
    url = "https://kick.com/xqc/videos/01a20000-1234-5678-1234-000000000001"
    FeedWatcher(
        store,
        lambda channel: [
            {"id": "01a20000-1234-5678-1234-000000000001", "url": url, "title": "Smaller VOD", "published": 1}
        ],
    ).tick()
    assert store.one("SELECT count(*) n FROM jobs")["n"] == 2

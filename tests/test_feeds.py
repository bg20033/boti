import json

import pytest
from pydantic import ValidationError

from boti.db import Store
from boti.feeds import FeedConfig, FeedWatcher, discover


def video(index, channel="deenthegreat"):
    identity = f"01a20000-1234-5678-1234-{index:012d}"
    return {
        "id": identity,
        "url": f"https://kick.com/{channel}/videos/{identity}",
        "title": f"Recording {index}",
        "published": index,
    }


def feed(client, headers, **changes):
    body = {"channel": "deenthegreat", "group_ids": ["pc-1-g1"], **changes}
    result = client.post("/api/feeds", headers=headers, json=body)
    assert result.status_code == 200, result.text
    return result.json()["id"]


@pytest.mark.parametrize("name", ["DeenTheGreat", "@deenthegreat", "https://kick.com/deenthegreat/videos"])
def test_feed_channel_normalization(name):
    assert FeedConfig(channel=name, group_ids=["pc-1-g1"]).channel == "deenthegreat"


@pytest.mark.parametrize(
    "name",
    [
        "https://localhost/channel",
        "https://kick.com.evil.test/a",
        "https://user@kick.com/a",
        "https://kick.com/a?next=evil",
        "https://kick.com/a:8765",
        "https://kick.com:443/a",
        "https://kick.com/a/videos/123",
        "https://youtube.com/@a",
    ],
)
def test_feed_rejects_non_channel_input(name):
    with pytest.raises(ValidationError):
        FeedConfig(channel=name, group_ids=["pc-1-g1"])


def test_discovery_keeps_only_finished_public_vods(monkeypatch):
    entries = []
    for index, changes in enumerate(
        [
            {},
            {"is_live": True},
            {"availability": "private"},
            {"duration": 0},
            {"live_status": "post_live"},
            {"url": video(99, "other")["url"]},
        ],
        1,
    ):
        item = video(index)
        entries.append(
            {
                **item,
                "_type": "url_transparent",
                "duration": 300,
                "is_live": False,
                "availability": "public",
                "timestamp": index,
                **changes,
            }
        )
    options = {}

    class Downloader:
        def __init__(self, config):
            options.update(config)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            assert url == "https://kick.com/deenthegreat/videos"
            assert download is False
            return {"entries": iter(entries)}

    monkeypatch.setattr("yt_dlp.YoutubeDL", Downloader)
    result = discover("deenthegreat")
    assert [item["id"] for item in result] == [video(1)["id"]]
    assert options["playlistend"] == 50


def test_feed_refills_queue_without_waiting_for_next_poll(client, store, admin_headers):
    identity = feed(client, admin_headers)
    calls = []

    def fetch(channel):
        calls.append(channel)
        return [video(3), video(2), video(1)]

    FeedWatcher(store, fetch).tick()
    assert store.one("SELECT count(*) n FROM jobs")["n"] == 2
    assert store.one("SELECT count(*) n FROM feed_items WHERE state='pending'")["n"] == 1
    reopened = Store(store.config)
    reopened.execute("UPDATE jobs SET status='complete'")
    FeedWatcher(reopened, fetch).tick()
    assert calls == ["deenthegreat"]
    assert reopened.one("SELECT count(*) n FROM jobs")["n"] == 3
    FeedWatcher(reopened, fetch).tick()
    assert reopened.one("SELECT count(*) n FROM jobs")["n"] == 3
    row = client.get("/api/feeds", headers=admin_headers).json()[0]
    assert row["id"] == identity and row["pending"] == 0 and row["imported"] == 3


def test_first_poll_can_skip_history_but_import_new_videos(client, store, admin_headers):
    identity = feed(client, admin_headers, include_existing=False)
    FeedWatcher(store, lambda _: [video(1)]).tick()
    assert store.one("SELECT count(*) n FROM jobs")["n"] == 0
    client.post(f"/api/feeds/{identity}/check", headers=admin_headers)
    FeedWatcher(store, lambda _: [video(2), video(1)]).tick()
    assert store.one("SELECT count(*) n FROM jobs")["n"] == 1
    assert store.one("SELECT video_id FROM jobs")["video_id"] == video(2)["id"]


def test_refill_gives_other_channels_a_turn(client, store, admin_headers):
    feed(client, admin_headers, pending_limit=1)
    feed(client, admin_headers, channel="gmhikaru", pending_limit=1)
    watcher = FeedWatcher(store, lambda channel: [video(3, channel), video(2, channel)])
    watcher.tick()
    first = store.one("SELECT url FROM jobs")["url"]
    store.execute("UPDATE jobs SET status='complete'")
    watcher.tick()
    following = store.one("SELECT url FROM jobs WHERE status='queued'")["url"]
    assert first.split("/")[3] != following.split("/")[3]


def test_discovery_failure_does_not_starve_other_channels(client, store, admin_headers):
    feed(client, admin_headers)
    feed(client, admin_headers, channel="gmhikaru")

    def fetch(channel):
        if channel == "deenthegreat":
            raise ValueError("Channel temporarily unavailable")
        return [video(1, channel)]

    FeedWatcher(store, fetch).tick()
    assert store.one("SELECT count(*) n FROM jobs")["n"] == 1
    assert "unavailable" in store.one("SELECT error FROM feeds WHERE channel='deenthegreat'")["error"]


def test_queued_import_survives_network_outage(client, store, admin_headers):
    identity = feed(client, admin_headers, pending_limit=1)
    FeedWatcher(store, lambda _: [video(2), video(1)]).tick()
    store.execute("UPDATE jobs SET status='complete'")
    client.post(f"/api/feeds/{identity}/check", headers=admin_headers)

    def offline(channel):
        raise ConnectionError("Offline")

    FeedWatcher(store, offline).tick()
    assert store.one("SELECT count(*) n FROM jobs")["n"] == 2


def test_recovery_deduplicates_job_created_before_item_checkpoint(client, store, admin_headers, monkeypatch):
    feed(client, admin_headers)
    original = store.execute
    interrupted = False

    def execute(sql, args=()):
        nonlocal interrupted
        if sql.startswith("UPDATE feed_items SET state='imported'") and not interrupted:
            interrupted = True
            raise RuntimeError("Process interrupted after durable job creation")
        return original(sql, args)

    monkeypatch.setattr(store, "execute", execute)
    FeedWatcher(store, lambda _: [video(1)]).tick()
    assert store.one("SELECT count(*) n FROM jobs")["n"] == 1
    FeedWatcher(Store(store.config), lambda _: [video(1)]).tick()
    assert store.one("SELECT count(*) n FROM jobs")["n"] == 1
    assert store.one("SELECT state FROM feed_items")["state"] == "imported"


@pytest.mark.parametrize("reason", ["global_pause", "disk", "disabled"])
def test_feed_respects_pause_and_disk_limits(client, store, admin_headers, reason):
    feed(client, admin_headers, enabled=reason != "disabled")
    settings = store.settings()
    settings.paused = reason == "global_pause"
    settings.min_free_gb = 1e9 if reason == "disk" else 0
    store.save_settings(settings)
    calls = []
    FeedWatcher(store, lambda channel: calls.append(channel) or [video(1)]).tick()
    assert not calls
    assert store.one("SELECT count(*) n FROM jobs")["n"] == 0


def test_feed_can_be_disabled_while_group_is_paused(client, store, admin_headers):
    identity = feed(client, admin_headers)
    store.execute("UPDATE groups SET paused=1")
    result = client.put(
        f"/api/feeds/{identity}",
        headers=admin_headers,
        json={
            "channel": "deenthegreat",
            "group_ids": ["pc-1-g1"],
            "enabled": False,
        },
    )
    assert result.status_code == 200


def test_feed_routes_require_admin_and_preserve_jobs_on_delete(client, store, admin_headers):
    assert client.get("/api/feeds").status_code == 401
    assert (
        client.post("/api/feeds", json={"channel": "deenthegreat", "group_ids": ["pc-1-g1"]}).status_code
        == 401
    )
    identity = feed(client, admin_headers)
    assert (
        client.post(
            "/api/feeds",
            headers=admin_headers,
            json={
                "channel": "@DeenTheGreat",
                "group_ids": ["pc-1-g1"],
            },
        ).status_code
        == 409
    )
    FeedWatcher(store, lambda _: [video(1)]).tick()
    assert client.delete(f"/api/feeds/{identity}", headers=admin_headers).status_code == 200
    assert store.one("SELECT count(*) n FROM jobs")["n"] == 1
    assert store.one("SELECT count(*) n FROM feed_items")["n"] == 0


def test_feed_trial_uses_existing_non_publishing_workflow(client, store, admin_headers):
    feed(client, admin_headers, trial={"start_seconds": 60, "duration_seconds": 180, "max_clips": 3})
    FeedWatcher(store, lambda _: [video(1)]).tick()
    job = store.one("SELECT * FROM jobs")
    assert job["run_mode"] == "trial" and job["trial_start"] == 60 and job["trial_seconds"] == 180
    assert store.one("SELECT count(*) n FROM publications")["n"] == 0


def test_feed_poll_ignores_results_if_configuration_changed(client, store, admin_headers):
    identity = feed(client, admin_headers)

    def fetch(channel):
        current = client.get("/api/feeds", headers=admin_headers).json()[0]["data"]
        client.put(f"/api/feeds/{identity}", headers=admin_headers, json={**current, "enabled": False})
        return [video(1)]

    FeedWatcher(store, fetch).tick()
    assert store.one("SELECT count(*) n FROM feed_items")["n"] == 0
    assert store.one("SELECT count(*) n FROM jobs")["n"] == 0


def test_feed_uses_campaign_rules_and_destinations(client, store, admin_headers):
    campaign = client.post(
        "/api/campaigns",
        headers=admin_headers,
        json={
            "name": "Deen",
            "group_ids": ["pc-1-g1"],
            "clip": {"allowed_sources": ["https://kick.com/deenthegreat"]},
        },
    ).json()["id"]
    assert (
        client.post(
            "/api/feeds",
            headers=admin_headers,
            json={
                "channel": "gmhikaru",
                "group_ids": ["pc-1-g1"],
                "campaign_id": campaign,
            },
        ).status_code
        == 422
    )
    feed(client, admin_headers, campaign_id=campaign)
    FeedWatcher(store, lambda _: [video(1)]).tick()
    linked = store.one("SELECT * FROM job_campaigns")
    assert linked["campaign_id"] == campaign
    assert json.loads(linked["rules_snapshot"])["clip"]["allowed_sources"] == [
        "https://kick.com/deenthegreat"
    ]

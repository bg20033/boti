import json
import sqlite3
import time

import httpx
import pytest

from boti.campaigns import publish_allowed
from boti.db import SCHEMA, Store, encode, uid
from boti.pipeline import Pipeline
from boti.quality import caption_alignment, repair_metadata
from boti.sources import validate_info, vod_url

KICK = "https://kick.com/deenthegreat/videos/01a1b7a3-f0e8-7254-aef0-2384158ccac2"


def verdict(passed, repair="layout"):
    return {
        "verdict": {
            "approved": passed,
            "score": 90 if passed else 50,
            "reasons": ["fixture pass" if passed else "fixture rejection"],
            "repair": repair,
        },
        "technical_passed": True,
        "technical": {"fixture": True},
        "model": "test",
    }


def automatic_job(store, monkeypatch, campaign=None):
    job, _ = store.create_job(
        "https://twitch.tv/videos/777", "777", ["pc-1-g1"], campaign, review_mode="automatic"
    )
    directory = store.config.root / "media" / job
    directory.mkdir(exist_ok=True)
    source = directory / "source.mp4"
    source.write_bytes(b"original")
    store.execute(
        "UPDATE jobs SET status='processing',stage='render',metadata=? WHERE id=?",
        (encode({"source": str(source.relative_to(store.config.root)), "duration": 90}), job),
    )
    meta = {"start": 10, "end": 40, "score": 90, "title": "fixture", "segments": [], "content_type": "mixed"}
    clip = uid()
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,created,updated) VALUES (?,?,'pc-1-g1',10,40,90,?,0,0)",
        (clip, job, encode(meta)),
    )
    monkeypatch.setattr(
        "boti.pipeline.cut_segment", lambda src, dest, start, length: dest.write_bytes(b"segment")
    )
    for i in range(1, 5):
        store.heartbeat(f"pc-{i}", ["render", "quality", "repair"])
    return job, clip


def finish_render(store):
    task = store.claim("pc-1")
    assert task["kind"] == "render"
    path = store.config.root / "media" / task["job_id"] / f"{task['id']}.mp4"
    path.write_bytes(b"verified-output" + str(task["payload"]["version"]).encode())
    asset = store.add_asset(task["job_id"], path, task["id"])
    store.finish(task["id"], "pc-1", task["lease_token"], result={"asset_id": asset["id"]})
    return task


def finish_quality(store, passed):
    task = store.claim("pc-1")
    assert task["kind"] == "quality"
    store.finish(task["id"], "pc-1", task["lease_token"], result=verdict(passed))
    return task


@pytest.mark.parametrize(
    "outcomes,expected,versions",
    [([True], "ready", 1), ([False, True], "ready", 2), ([False, False], "skipped", 2)],
)
def test_automatic_lifecycle_is_restart_safe(store, monkeypatch, outcomes, expected, versions):
    job, clip = automatic_job(store, monkeypatch)
    pipeline = Pipeline(store)
    pipeline.advance()
    for index, passed in enumerate(outcomes):
        finish_render(store)
        pipeline.advance()
        assert store.one("SELECT status FROM clips WHERE id=?", (clip,))["status"] == "checking"
        assert store.one("SELECT count(*) n FROM publications")["n"] == 0
        finish_quality(store, passed)
        pipeline = Pipeline(Store(store.config))
        pipeline.advance()
        pipeline.advance()  # Completed results may be observed repeatedly.
        if not passed and index == 0:
            task = store.claim("pc-1")
            assert task["kind"] == "repair"
            original = task["payload"]["metadata"]
            result = repair_metadata(task["payload"], None, None)
            assert result["metadata"]["title"] == original["title"]
            assert result["metadata"]["force_fit"] is True
            store.finish(task["id"], "pc-1", task["lease_token"], result=result)
            pipeline = Pipeline(Store(store.config))
            pipeline.advance()
    row = store.one("SELECT * FROM clips WHERE id=?", (clip,))
    assert row["status"] == expected and row["current_version"] == versions
    assert store.one("SELECT status FROM jobs WHERE id=?", (job,))["status"] == "complete"
    assert store.one("SELECT count(*) n FROM quality_reports")["n"] == len(outcomes)
    assert store.one("SELECT count(*) n FROM reviews")["n"] == 0
    assert store.one("SELECT count(*) n FROM publications")["n"] == (4 if expected == "ready" else 0)
    assert store.one("SELECT count(*) n FROM tasks WHERE kind='repair'")["n"] == versions - 1
    assert publish_allowed(store, row) == (expected == "ready")
    pipeline.advance()
    assert store.one("SELECT count(*) n FROM clip_versions")["n"] == versions


def test_migration_preserves_legacy_rows_and_unique_provider_identity(tmp_path):
    from boti.config import Config

    config = Config(tmp_path / "old")
    with sqlite3.connect(config.db_path) as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO jobs(id,source_id,url,group_ids,created,updated) VALUES ('old','123','https://twitch.tv/videos/123','[]',0,0)"
        )
        db.execute(
            "INSERT INTO tasks(id,job_id,kind,task_key,payload,created,updated) VALUES ('task','old','transcribe','0','{}',0,0)"
        )
    store = Store(config)
    old = store.one("SELECT * FROM jobs WHERE id='old'")
    assert old["provider"] == "twitch" and old["video_id"] == "123" and old["review_mode"] == "manual"
    assert store.create_job(old["url"], "123", [])[0] == "old"
    other, created = store.create_job(KICK, "123", [], provider="kick", review_mode="automatic")
    assert created and other != "old"
    Store(config)
    assert store.one("SELECT job_id FROM tasks WHERE id='task'")["job_id"] == "old"
    with store.connect() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    "url",
    [
        "https://kick.com/channel",
        "https://kick.com/channel/clips/abc",
        "https://kick.com.evil.test/a/videos/123",
        "https://u@kick.com/a/videos/123",
        "http://kick.com/a/videos/123",
    ],
)
def test_reject_non_vod_kick(url):
    with pytest.raises(ValueError):
        vod_url(url)


def test_kick_api_detection_and_no_duplicate(client, admin_headers, store):
    data = {"url": KICK, "group_ids": ["pc-1-g1"]}
    result = client.post("/api/jobs", headers=admin_headers, json=data)
    assert result.status_code == 200
    job = store.one("SELECT * FROM jobs WHERE id=?", (result.json()["id"],))
    assert job["provider"] == "kick" and job["review_mode"] == "automatic"
    data["url"] += "?t=10"
    assert client.post("/api/jobs", headers=admin_headers, json=data).json()["created"] is False
    data["provider"] = "twitch"
    assert client.post("/api/jobs", headers=admin_headers, json=data).status_code == 422


@pytest.mark.parametrize(
    "info",
    [None, {"is_live": True}, {"live_status": "post_live"}, {"availability": "private"}, {"duration": 0}],
)
def test_download_rejects_unfinished_or_inaccessible(info):
    with pytest.raises(ValueError):
        validate_info(info)


def test_failed_provider_is_terminal_not_an_infinite_loop(store, monkeypatch):
    job, _ = store.create_job(KICK, "uuid", ["pc-1-g1"], provider="kick")
    calls = []

    def fail(item):
        calls.append(item["id"])
        raise ValueError("Private VOD")

    pipeline = Pipeline(store)
    monkeypatch.setattr(pipeline, "ingest", fail)
    pipeline.ingest_next()
    pipeline.ingest_next()
    assert calls == [job]
    assert store.one("SELECT status FROM jobs WHERE id=?", (job,))["status"] == "failed"


def test_quality_lease_fences_reassigned_worker_and_preserves_one_correction(store, monkeypatch):
    job, clip = automatic_job(store, monkeypatch)
    pipe = Pipeline(store)
    pipe.advance()
    finish_render(store)
    pipe.advance()
    stale = store.claim("pc-1")
    store.execute("UPDATE tasks SET lease_expires=0 WHERE id=?", (stale["id"],))
    task = store.claim("pc-2")
    with pytest.raises(ValueError):
        store.finish(stale["id"], "pc-1", stale["lease_token"], result=verdict(True))
    store.finish(task["id"], "pc-2", task["lease_token"], result=verdict(False))
    for _ in range(3):
        Pipeline(Store(store.config)).advance()
    assert store.one("SELECT count(*) n FROM tasks WHERE kind='repair'")["n"] == 1
    assert store.one("SELECT current_version FROM clips WHERE id=?", (clip,))["current_version"] == 1


def test_technical_failure_is_separate_and_does_not_fail_entire_vod(store, monkeypatch):
    job, clip = automatic_job(store, monkeypatch)
    pipe = Pipeline(store)
    pipe.advance()
    for _ in range(3):
        task = store.claim("pc-1")
        store.finish(task["id"], "pc-1", task["lease_token"], error="GPU unavailable")
        store.execute("UPDATE tasks SET not_before=0 WHERE id=?", (task["id"],))
    pipe.advance()
    assert store.one("SELECT status FROM clips WHERE id=?", (clip,))["status"] == "technical_failed"
    assert store.one("SELECT status FROM jobs WHERE id=?", (job,))["status"] == "complete"
    assert store.one("SELECT count(*) n FROM tasks WHERE kind='repair'")["n"] == 0


def test_automatic_gate_binds_artifact_metadata_rules_and_manual_veto(
    store, client, admin_headers, monkeypatch
):
    response = client.post(
        "/api/campaigns",
        headers=admin_headers,
        json={
            "name": "Test",
            "group_ids": ["pc-1-g1"],
            "status": "active",
            "rules": "Allowed original footage",
            "rules_confirmed": True,
            "automation_confirmed": True,
        },
    )
    campaign = response.json()["id"]
    job, clip_id = automatic_job(store, monkeypatch, campaign)
    pipe = Pipeline(store)
    pipe.advance()
    finish_render(store)
    pipe.advance()
    finish_quality(store, True)
    pipe.advance()
    clip = store.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    assert publish_allowed(store, clip)
    store.execute("UPDATE assets SET sha256='different' WHERE id=?", (clip["asset_id"],))
    assert not publish_allowed(store, clip)
    sha = store.one("SELECT sha256 FROM quality_reports")["sha256"]
    store.execute("UPDATE assets SET sha256=? WHERE id=?", (sha, clip["asset_id"]))
    meta = json.loads(clip["metadata"])
    store.execute(
        "UPDATE clips SET metadata=? WHERE id=?", (encode({**meta, "title": "Changed title"}), clip_id)
    )
    assert not publish_allowed(store, clip)
    store.execute("UPDATE clips SET metadata=? WHERE id=?", (clip["metadata"], clip_id))
    assert publish_allowed(store, clip)
    client.post(f"/api/clips/{clip_id}/review", headers=admin_headers, json={"passed": False})
    data = client.get("/api/campaigns", headers=admin_headers).json()[0]["data"]
    data["rules"] = "Updated requirements"
    client.put(f"/api/campaigns/{campaign}", headers=admin_headers, json=data)
    assert not publish_allowed(store, clip)
    client.post(f"/api/jobs/{job}/campaign-review", headers=admin_headers)
    assert store.one("SELECT passed FROM reviews WHERE clip_id=?", (clip_id,))["passed"] == 0
    pipe.advance()
    finish_quality(store, True)
    pipe.advance()
    assert not publish_allowed(store, clip)
    assert store.one("SELECT count(*) n FROM publications")["n"] == 4
    assert store.one("SELECT count(*) n FROM quality_reports")["n"] == 2
    assert client.get(f"/api/clips/{clip_id}/tiktok.zip", headers=admin_headers).status_code == 409


def test_alignment_catches_wrong_words_and_shifted_captions():
    words = [{"word": w, "start": i, "end": i + 0.5} for i, w in enumerate(["a", "complete", "sentence"])]
    original = [{"words": words}]
    assert caption_alignment(original, original)["passed"]
    assert not caption_alignment(original, [{"words": [{**w, "start": w["start"] + 2} for w in words]}])[
        "passed"
    ]
    assert not caption_alignment(original, [{"words": [{**w, "word": "different"} for w in words]}])["passed"]


@pytest.mark.parametrize("platform", ["youtube", "instagram"])
def test_automatic_publication_does_not_require_human_approval(store, monkeypatch, platform):
    from boti.publishing import Publisher

    _, clip = automatic_job(store, monkeypatch)
    pipe = Pipeline(store)
    pipe.advance()
    finish_render(store)
    pipe.advance()
    finish_quality(store, True)
    pipe.advance()
    settings = store.settings()
    settings.publishing_enabled = True
    store.save_settings(settings)
    store.execute(
        "UPDATE accounts SET status='connected',credentials=? WHERE group_id='pc-1-g1' AND platform=?",
        (store.config.encrypt({}), platform),
    )
    publisher = Publisher(
        store, httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected external call")))
    )
    calls = []

    def publish(*args):
        calls.append(1)
        return {"id": "test", "url": "https://example.test/result"}

    monkeypatch.setattr(publisher, platform, publish)
    publisher.tick()
    publisher.tick()
    assert calls == [1]
    assert store.one("SELECT count(*) n FROM reviews")["n"] == 0
    assert store.one("SELECT status FROM publications WHERE platform=?", (platform,))["status"] == "published"


def test_changed_file_bytes_invalidate_automatic_approval(store, monkeypatch):
    _, clip_id = automatic_job(store, monkeypatch)
    pipe = Pipeline(store)
    pipe.advance()
    finish_render(store)
    pipe.advance()
    finish_quality(store, True)
    pipe.advance()
    clip = store.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    assert publish_allowed(store, clip)
    asset = store.one("SELECT * FROM assets WHERE id=?", (clip["asset_id"],))
    path = store.config.path(asset["path"])
    path.write_bytes(b"X" * asset["size"])
    assert not publish_allowed(store, clip)


def test_completed_download_checkpoint_avoids_provider_after_restart(store, monkeypatch):
    from boti.pipeline import DownloadPaused

    job, _ = store.create_job(KICK, "checkpoint", ["pc-1-g1"], provider="kick")
    calls = []

    class Downloader:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download=False, ie_key=None):
            calls.append(download)
            if download:
                from pathlib import Path

                Path(self.prepare_filename({})).write_bytes(b"downloaded")
            return {"duration": 30, "uploader_id": "test", "title": "checkpoint"}

        def prepare_filename(self, info):
            return self.options["outtmpl"].replace("%(ext)s", "mp4")

    monkeypatch.setattr("yt_dlp.YoutubeDL", Downloader)
    monkeypatch.setattr("boti.pipeline.duration", lambda _: 30)
    monkeypatch.setattr("boti.pipeline.extract_audio", lambda src, dst: dst.write_bytes(b"audio"))
    monkeypatch.setattr("boti.pipeline.audio_slice", lambda src, dst, *args: dst.write_bytes(b"slice"))
    pipe = Pipeline(store)
    checks = iter([True, False])
    monkeypatch.setattr(pipe, "has_space", lambda *args: next(checks))
    with pytest.raises(DownloadPaused):
        pipe.ingest(store.one("SELECT * FROM jobs WHERE id=?", (job,)))
    assert calls == [False, True]
    pipe = Pipeline(Store(store.config))
    pipe.ingest_next()
    assert calls == [False, True]
    assert store.one("SELECT status FROM jobs WHERE id=?", (job,))["status"] == "processing"


def test_caption_repair_uses_source_audio_not_generated_text(monkeypatch):
    from types import SimpleNamespace

    words = [{"start": 1, "end": 2, "word": "original"}]
    calls = []

    def transcribe(path, *args, **kwargs):
        calls.append(path)
        return {"segments": [{"start": 1, "end": 2, "text": "original", "words": words}]}

    monkeypatch.setattr("boti.ai.transcribe", transcribe)
    result = repair_metadata(
        {"repair": "captions", "metadata": {"start": 10, "end": 40, "title": "preserved"}},
        "original-segment.mp4",
        SimpleNamespace(whisper_model="medium", whisper_device="cpu"),
    )
    assert calls == ["original-segment.mp4"]
    assert result["metadata"]["segments"][0]["words"] == words
    assert result["metadata"]["title"] == "preserved"


def test_no_new_render_when_quality_queue_fills_capacity(store, monkeypatch):
    job, clip = automatic_job(store, monkeypatch)
    settings = store.settings()
    settings.max_prepared_tasks = 1
    store.save_settings(settings)
    pipe = Pipeline(store)
    pipe.advance()
    finish_render(store)
    pipe.advance()
    other = uid()
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,created,updated) SELECT ?,job_id,group_id,start,end,score,metadata,created,updated FROM clips WHERE id=?",
        (other, clip),
    )
    pipe.advance()
    assert store.one("SELECT status FROM clips WHERE id=?", (other,))["status"] == "preparing"
    assert store.one("SELECT count(*) n FROM tasks WHERE kind='render'")["n"] == 1


def test_cleanup_preserves_old_partial_for_paused_download(store):
    import os

    job, _ = store.create_job(KICK, "partial", ["pc-1-g1"], provider="kick")
    directory = store.config.root / "media" / job
    directory.mkdir()
    partial = directory / "source.mp4.part"
    partial.write_bytes(b"resume this")
    old = time.time() - 2 * 86400
    os.utime(partial, (old, old))
    pipe = Pipeline(store)
    pipe.cleanup()
    assert partial.exists()
    store.execute("UPDATE jobs SET status='failed' WHERE id=?", (job,))
    pipe.cleanup()
    assert not partial.exists()

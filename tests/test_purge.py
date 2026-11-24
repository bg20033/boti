import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest

from boti.archive import request_restore
from boti.db import uid
from boti.purge import purge_generated, service_one


def old_clip(store):
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    folder = store.config.root / "media" / job
    folder.mkdir()
    (folder / "source.mp4").write_bytes(b"old source")
    output = folder / "output.mp4"
    output.write_bytes(b"old generated clip")
    asset = store.add_asset(job, output)
    clip = uid()
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,status,asset_id,created,updated) VALUES(?,?,'pc-1-g1',0,30,90,'{}','ready',?,?,?)",
        (clip, job, asset["id"], time.time(), time.time()),
    )
    store.execute("UPDATE jobs SET status='complete',completed=? WHERE id=?", (time.time(), job))
    store.execute(
        "INSERT INTO worker_archives VALUES(?,?,?,?,?,?)",
        (asset["id"], "pc-1", asset["sha256"], asset["size"], time.time(), time.time()),
    )
    return job, clip, asset


def pause(store):
    settings = store.settings()
    settings.paused = True
    store.save_settings(settings)


def test_purge_removes_generated_media_and_library_but_preserves_inbox_and_credentials(
    client, store, admin_headers
):
    job, clip, asset = old_clip(store)
    inbox = store.config.root / "inbox" / "personal.mp4"
    inbox.write_bytes(b"input stays")
    token = (store.config.root / "secrets" / "admin.token").read_bytes()
    with pytest.raises(ValueError):
        purge_generated(store)
    pause(store)
    result = purge_generated(store)
    assert result["clips_deleted"] == 1 and result["local_failures"] == []
    assert not (store.config.root / "media" / job).exists()
    assert inbox.read_bytes() == b"input stays"
    assert (store.config.root / "secrets" / "admin.token").read_bytes() == token
    assert client.get("/api/clips", headers=admin_headers).json()["total"] == 0
    assert client.get(f"/api/clips/{clip}/video", headers=admin_headers).status_code == 410
    assert not request_restore(store, asset)
    assert not store.all("SELECT * FROM archive_restores")
    fresh, created = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    assert created and fresh != job
    assert store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"]) == (fresh, False)


def test_purge_preserves_queued_job_identity_and_fences_old_running_work(store):
    job, _, _ = old_clip(store)
    store.execute("UPDATE jobs SET status='processing' WHERE id=?", (job,))
    task = store.add_task(job, "transcribe", "work", {})
    store.heartbeat("pc-1", ["transcribe"], {})
    lease = store.claim("pc-1")
    queued, _ = store.create_job("https://twitch.tv/videos/456", "456", ["pc-1-g1"])
    pause(store)
    purge_generated(store)
    assert store.one("SELECT status FROM tasks WHERE id=?", (task,))["status"] == "failed"
    with pytest.raises(ValueError):
        store.finish(task, "pc-1", lease["lease_token"], {})
    assert store.create_job("https://twitch.tv/videos/456", "456", ["pc-1-g1"]) == (queued, False)
    purge_generated(store)
    assert store.create_job("https://twitch.tv/videos/456", "456", ["pc-1-g1"]) == (queued, False)


def test_worker_deletion_is_scoped_authenticated_and_durable(client, store, worker_headers, tmp_path):
    job, _, asset = old_clip(store)
    task = store.add_task(job, "render", "old-task", {})
    store.execute("UPDATE tasks SET worker_id='pc-1',status='complete' WHERE id=?", (task,))
    pause(store)
    purge_generated(store)
    assert client.post("/api/worker/archive/delete/claim").status_code == 401
    assert (
        client.post("/api/worker/archive/delete/claim", headers=worker_headers["pc-2"]).json()["delete"]
        is None
    )
    folder = tmp_path / "worker-archive"
    folder.mkdir()
    output = folder / f"{asset['sha256']}.mp4"
    output.write_bytes(b"old generated clip")
    keep = folder / "unrelated.txt"
    keep.write_text("keep")
    cache = tmp_path / "cache"
    (cache / task).mkdir(parents=True)
    (cache / task / "copy.mp4").write_bytes(b"old task cache")

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://test",
            headers=worker_headers["pc-1"],
        ) as http:
            worker = SimpleNamespace(client=http, archive_dir=folder, cache=cache)
            assert await service_one(worker)
            assert not await service_one(worker)
            assert worker.cache_purge_generation == 1

    asyncio.run(run())
    assert not output.exists() and keep.read_text() == "keep"
    assert not (cache / task).exists()
    assert store.one("SELECT status FROM archive_deletions WHERE worker_id='pc-1'")["status"] == "done"


def test_new_reference_to_same_archive_hash_preserves_new_video(client, store, worker_headers):
    _, _, asset = old_clip(store)
    pause(store)
    purge_generated(store)
    new, _ = store.create_job("https://twitch.tv/videos/456", "456", ["pc-1-g1"])
    folder = store.config.root / "media" / new
    folder.mkdir()
    path = folder / "new.mp4"
    path.write_bytes(b"old generated clip")
    fresh = store.add_asset(new, path)
    store.execute(
        "INSERT INTO worker_archives VALUES(?,?,?,?,?,?)",
        (fresh["id"], "pc-1", fresh["sha256"], fresh["size"], time.time(), time.time()),
    )
    assert (
        client.post("/api/worker/archive/delete/claim", headers=worker_headers["pc-1"]).json()["delete"]
        is None
    )
    assert store.one("SELECT status FROM archive_deletions WHERE worker_id='pc-1'")["status"] == "kept"

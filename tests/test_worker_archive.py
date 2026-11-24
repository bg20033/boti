import shutil
import time

from boti.archive import prune_cache, request_restore, retain
from boti.db import encode, uid


def archived(store, tmp_path):
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    output = store.config.root / "media" / "rendered.mp4"
    output.write_bytes(b"verified output bytes")
    kept = retain(output, tmp_path / "mint-archive")
    asset = store.add_asset(job, output)
    store.execute(
        "INSERT INTO worker_archives VALUES (?,?,?,?,?,?)",
        (asset["id"], "pc-1", kept["sha256"], kept["size"], time.time(), 0),
    )
    clip = uid()
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,status,asset_id,created,updated) VALUES (?,?,'pc-1-g1',0,30,90,?,'skipped',?,0,0)",
        (clip, job, encode({"title": "Retained output"}), asset["id"]),
    )
    return asset, output, tmp_path / "mint-archive" / f"{kept['sha256']}.mp4"


def test_archive_survives_task_cache_cleanup_and_main_cache_eviction(store, tmp_path):
    asset, output, kept = archived(store, tmp_path)
    task_cache = tmp_path / "task-cache"
    task_cache.mkdir()
    shutil.copyfile(output, task_cache / "output.mp4")
    shutil.rmtree(task_cache)
    assert kept.read_bytes() == output.read_bytes()
    prune_cache(store)
    assert not output.exists() and kept.is_file()
    assert not request_restore(store, asset)
    assert (
        store.one("SELECT worker_id FROM archive_restores WHERE asset_id=?", (asset["id"],))["worker_id"]
        == "pc-1"
    )


def test_corrupted_existing_archive_is_repaired_before_it_is_confirmed(store, tmp_path):
    asset, output, kept = archived(store, tmp_path)
    kept.write_bytes(b"x" * asset["size"])
    retain(output, kept.parent)
    assert kept.read_bytes() == output.read_bytes()


def test_restore_is_worker_scoped_fenced_and_hash_verified(store, client, worker_headers, tmp_path):
    asset, output, kept = archived(store, tmp_path)
    output.unlink()
    request_restore(store, asset)
    assert client.post("/api/worker/archive/claim").status_code == 401
    assert client.post("/api/worker/archive/claim", headers=worker_headers["pc-2"]).json()["restore"] is None
    item = client.post("/api/worker/archive/claim", headers=worker_headers["pc-1"]).json()["restore"]
    assert item["sha256"] == asset["sha256"]
    url = f"/api/worker/archive/{asset['id']}/restore"
    headers = {**worker_headers["pc-1"], "X-Restore-Token": item["token"]}
    assert (
        client.put(
            url,
            headers={**worker_headers["pc-2"], "X-Restore-Token": item["token"]},
            content=kept.read_bytes(),
        ).status_code
        == 409
    )
    assert client.put(url, headers=headers, content=b"x" * asset["size"]).status_code == 422
    assert not output.exists()
    assert client.put(url, headers=headers, content=kept.read_bytes()).status_code == 200
    assert output.read_bytes() == kept.read_bytes()
    assert client.put(url, headers=headers, content=kept.read_bytes()).status_code == 409


def test_cache_is_retained_for_active_quality_and_recent_access(store, tmp_path):
    asset, output, _ = archived(store, tmp_path)
    store.execute("UPDATE clips SET status='checking' WHERE asset_id=?", (asset["id"],))
    prune_cache(store)
    assert output.exists()
    store.execute("UPDATE clips SET status='skipped' WHERE asset_id=?", (asset["id"],))
    assert request_restore(store, asset)
    prune_cache(store)
    assert output.exists()

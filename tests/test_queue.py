import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from boti.db import Store, encode, uid


def job(store):
    job_id, _ = store.create_job("https://www.twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute("UPDATE jobs SET status='processing' WHERE id=?", (job_id,))
    return job_id


def test_seed_is_idempotent_and_secrets_are_not_in_snapshot(store):
    store.seed()
    assert store.one("SELECT count(*) n FROM groups")["n"] == 12
    assert store.one("SELECT count(*) n FROM accounts")["n"] == 36
    assert store.one("SELECT count(*) n FROM workers")["n"] == 4
    serialized = json.dumps(store.snapshot())
    assert "token_hash" not in serialized
    assert "refresh_token" not in serialized


def test_concurrent_claims_allow_one_heavy_task_per_pc(store, worker_headers):
    job_id = job(store)
    for index in range(12):
        store.add_task(job_id, "transcribe", index, {"index": index})
    identities = [f"pc-{i}" for _ in range(4) for i in range(1, 5)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        tasks = [task for task in pool.map(store.claim, identities) if task]
    assert len(tasks) == 4
    assert len({t["id"] for t in tasks}) == 4
    assert len({t["worker_id"] for t in tasks}) == 4


def test_expired_lease_is_fenced_and_destination_survives_reassignment(store, worker_headers):
    task_id = store.add_task(job(store), "render", "clip", {"group_id": "pc-1-g1"}, "pc-1")
    original = store.claim("pc-1")
    store.execute("UPDATE tasks SET lease_expires=? WHERE id=?", (time.time() - 1, task_id))
    with pytest.raises(ValueError):
        store.heartbeat("pc-1", task_id=task_id, lease_token=original["lease_token"])
    replacement = store.claim("pc-2")
    assert replacement["payload"]["group_id"] == "pc-1-g1"
    assert replacement["lease_token"] != original["lease_token"]
    with pytest.raises(ValueError):
        store.finish(task_id, "pc-1", original["lease_token"], result={})
    store.finish(task_id, "pc-2", replacement["lease_token"], result={"asset_id": "verified"})
    assert store.one("SELECT status FROM tasks WHERE id=?", (task_id,))["status"] == "complete"


def test_queue_persists_across_restart_and_three_failures_stop_retrying(store, worker_headers):
    task_id = store.add_task(job(store), "transcribe", "one", {})
    for _ in range(3):
        task = store.claim("pc-1")
        store.execute("UPDATE tasks SET lease_expires=0 WHERE id=?", (task_id,))
        store = Store(store.config)
        store.reap()
        assert task is not None
    assert store.one("SELECT status FROM tasks WHERE id=?", (task_id,))["status"] == "failed"
    assert store.claim("pc-1") is None


def test_pausing_keeps_pending_work(store, worker_headers):
    store.add_task(job(store), "transcribe", "one", {})
    settings = store.settings()
    settings.paused = True
    store.save_settings(settings)
    assert store.claim("pc-1") is None
    settings.paused = False
    store.save_settings(settings)
    assert store.claim("pc-1") is not None


def test_low_disk_keeps_ingestion_queued_and_preserves_retry(store, monkeypatch):
    from collections import namedtuple

    from boti.pipeline import Pipeline

    job_id, _ = store.create_job("https://twitch.tv/videos/789", "789", ["pc-1-g1"])
    pipeline = Pipeline(store)
    usage = namedtuple("usage", "total used free")
    settings = store.settings()
    settings.min_free_gb = 20
    store.save_settings(settings)
    monkeypatch.setattr("boti.pipeline.shutil.disk_usage", lambda _: usage(100, 99, 1))
    calls = []
    monkeypatch.setattr(pipeline, "ingest", lambda item: calls.append(item["id"]))
    pipeline.ingest_next()
    assert not calls
    assert store.one("SELECT status FROM jobs WHERE id=?", (job_id,))["status"] == "queued"
    monkeypatch.setattr("boti.pipeline.shutil.disk_usage", lambda _: usage(100 * 1024**3, 0, 100 * 1024**3))
    pipeline.ingest_next()
    assert calls == [job_id]


def test_reinitializing_main_updates_config_url_without_rotating_tokens(store):
    before = json.loads((store.config.root / "secrets/worker-1.json").read_text())
    store.seed("http://192.168.10.5:8765")
    after = json.loads((store.config.root / "secrets/worker-1.json").read_text())
    assert before["token"] == after["token"]
    assert after["coordinator_url"] == "http://192.168.10.5:8765"


def test_old_transcription_is_not_starved_by_new_preferred_renders(store, worker_headers):
    job_id = job(store)
    old = store.add_task(job_id, "transcribe", "old", {})
    store.execute("UPDATE tasks SET created=? WHERE id=?", (time.time() - 1900, old))
    store.add_task(job_id, "render", "new", {}, "pc-1")
    assert store.claim("pc-1")["id"] == old


def test_active_job_limit_keeps_downloads_queued(store, monkeypatch):
    from boti.pipeline import Pipeline

    active = job(store)
    queued, _ = store.create_job("https://twitch.tv/videos/456", "456", ["pc-1-g1"])
    settings = store.settings()
    settings.max_active_jobs = 1
    store.save_settings(settings)
    pipeline = Pipeline(store)
    calls = []
    monkeypatch.setattr(pipeline, "ingest", lambda item: calls.append(item["id"]))
    pipeline.ingest_next()
    assert calls == []
    store.execute("UPDATE jobs SET status='complete' WHERE id=?", (active,))
    pipeline.ingest_next()
    assert calls == [queued]


def test_render_preparation_is_bounded_across_jobs_and_restart(store, monkeypatch):
    from boti.pipeline import Pipeline

    settings = store.settings()
    settings.max_prepared_tasks = 3
    store.save_settings(settings)
    jobs = [job(store)]
    other, _ = store.create_job("https://twitch.tv/videos/456", "456", ["pc-2-g1"])
    store.execute("UPDATE jobs SET status='processing' WHERE id=?", (other,))
    jobs.append(other)
    for index, job_id in enumerate(jobs):
        (store.config.root / "media" / job_id).mkdir()
        for _ in range(5):
            store.execute(
                """INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,created,updated)
                VALUES (?,?,?,0,30,90,?,?,?)""",
                (
                    uid(),
                    job_id,
                    f"pc-{index + 1}-g1",
                    encode({"start": 0, "end": 30}),
                    time.time(),
                    time.time(),
                ),
            )
    cuts = []

    def cut(source, dest, start, length):
        dest.write_bytes(b"prepared segment")
        cuts.append(dest)

    monkeypatch.setattr("boti.pipeline.cut_segment", cut)
    pipeline = Pipeline(store)
    for job_id in jobs:
        pipeline.prepare_renders(store.one("SELECT * FROM jobs WHERE id=?", (job_id,)), None)
    assert len(cuts) == 3
    # Restart must keep the global queue bound, even on another job.
    pipeline = Pipeline(store)
    second = store.one("SELECT * FROM jobs WHERE id=?", (other,))
    pipeline.prepare_renders(second, None)
    assert len(cuts) == 3
    store.execute(
        "UPDATE clips SET status='ready' WHERE id=(SELECT id FROM clips WHERE status='rendering' LIMIT 1)"
    )
    pipeline.prepare_renders(second, None)
    assert len(cuts) == 4
    task = store.one("SELECT payload FROM tasks WHERE job_id=?", (other,))
    assert json.loads(task["payload"])["group_id"] == "pc-2-g1"


def test_stage_metrics_use_real_durations_and_live_capabilities(store, worker_headers):
    job_id = job(store)
    task_id = store.add_task(job_id, "analyze", "one", {})
    now = time.time()
    store.execute(
        "UPDATE tasks SET status='complete',started=?,finished=?,attempts=2 WHERE id=?",
        (now - 50, now - 10, task_id),
    )
    store.add_task(job_id, "analyze", "two", {})
    store.execute("UPDATE workers SET last_seen=0 WHERE id='pc-4'")
    metric = next(stage for stage in store.stage_metrics() if stage["kind"] == "analyze")
    assert metric["samples"] == 1 and metric["average_seconds"] == 40
    assert metric["queued"] == 1 and metric["retried"] == 1 and metric["capable_workers"] == 3


def test_proxy_preparation_waits_for_analysis_capacity(store, monkeypatch):
    from boti.pipeline import Pipeline

    job_id = job(store)
    directory = store.config.root / "media" / job_id
    directory.mkdir()
    source = directory / "source.mp4"
    source.write_bytes(b"source")
    store.execute(
        "UPDATE jobs SET metadata=? WHERE id=?",
        (encode({"source": str(source.relative_to(store.config.root))}), job_id),
    )
    audio = directory / "audio.wav"
    audio.write_bytes(b"fixture audio")
    asset = store.add_asset(job_id, audio)
    for index in range(5):
        task_id = store.add_task(
            job_id,
            "transcribe",
            index,
            {"index": index, "offset": index * 240, "duration": 300, "audio_id": asset["id"]},
        )
        store.execute(
            "UPDATE tasks SET status='complete',result=? WHERE id=?", (encode({"segments": []}), task_id)
        )
    settings = store.settings()
    settings.max_prepared_tasks = 2
    store.save_settings(settings)
    prepared = []

    def proxy(source, destination, *args):
        destination.write_bytes(b"proxy")
        prepared.append(destination)

    monkeypatch.setattr("boti.pipeline.make_proxy", proxy)
    pipeline = Pipeline(store)
    pipeline.advance()
    assert len(prepared) == 2
    pipeline.advance()
    assert len(prepared) == 2
    store.execute(
        "UPDATE tasks SET status='complete',result=? WHERE id=(SELECT id FROM tasks WHERE kind='analyze' LIMIT 1)",
        (encode({"candidates": []}),),
    )
    pipeline.advance()
    assert len(prepared) == 3

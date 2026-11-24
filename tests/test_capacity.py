import time

from boti.capacity import estimate
from boti.db import encode, uid


def test_parallel_quality_uses_two_modern_remote_workers_and_keeps_analysis_on_main(store):
    settings = store.settings()
    settings.analysis_worker_id = "pc-1"
    settings.quality_worker_id = "pc-2"
    settings.quality_parallel_workers = 2
    store.save_settings(settings)
    store.heartbeat("pc-1", ["analyze", "quality"], {"quality_revision": 1})
    store.heartbeat("pc-2", ["quality", "render"], {"quality_revision": 1})
    store.heartbeat("pc-3", ["quality", "render"], {"quality_revision": 1})
    store.heartbeat("pc-4", ["quality"], {})
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute("UPDATE jobs SET status='processing' WHERE id=?", (job,))
    q1 = store.add_task(job, "quality", "first", {})
    q2 = store.add_task(job, "quality", "second", {})
    analysis = store.add_task(job, "analyze", "moments", {})
    assert store.claim("pc-2")["id"] == q1
    assert store.claim("pc-3")["id"] == q2
    assert store.claim("pc-4") is None
    assert store.claim("pc-1")["id"] == analysis
    store.heartbeat("pc-4", ["quality"], {"quality_revision": 1})
    store.add_task(job, "quality", "third", {})
    assert store.claim("pc-4") is None  # The selected two-worker pool remains bounded.


def test_parallel_quality_falls_back_when_remote_workers_are_offline(store):
    settings = store.settings()
    settings.analysis_worker_id = "pc-1"
    settings.quality_worker_id = "pc-2"
    settings.quality_parallel_workers = 2
    store.save_settings(settings)
    store.heartbeat("pc-1", ["analyze", "quality"], {"quality_revision": 1})
    store.heartbeat("pc-2", ["quality"], {})  # A legacy worker cannot use new checks.
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute("UPDATE jobs SET status='processing' WHERE id=?", (job,))
    task = store.add_task(job, "quality", "fallback", {})
    assert store.claim("pc-1")["id"] == task


def test_empty_capacity_is_unknown_instead_of_promising_output(store):
    result = estimate(store)
    assert result["processed_low"] is None and result["quality_scenarios"] == []
    assert result["current_automatic_posts"] == 0
    assert result["operator_posts_per_account_daily"] == 3


def test_dedicated_quality_worker_requires_new_revision_and_falls_back_to_main(store):
    all_roles = ["transcribe", "analyze", "render", "repair", "quality"]
    s = store.settings()
    s.analysis_worker_id = "pc-1"
    s.quality_worker_id = "pc-2"
    store.save_settings(s)
    store.heartbeat("pc-1", all_roles, {"quality_revision": 1})
    store.heartbeat("pc-2", all_roles, {"quality_revision": 0})
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute("UPDATE jobs SET status='processing' WHERE id=?", (job,))
    quality = store.add_task(job, "quality", "q", {})
    assert store.claim("pc-2") is None
    store.heartbeat("pc-2", all_roles, {"quality_revision": 1})
    assert store.claim("pc-2")["id"] == quality
    analyze = store.add_task(job, "analyze", "a", {})
    assert store.claim("pc-1")["id"] == analyze
    snapshot = store.snapshot()
    roles = {w["id"]: w["assigned_capabilities"] for w in snapshot["workers"]}
    assert "analyze" in roles["pc-1"] and "quality" not in roles["pc-1"]
    assert "quality" in roles["pc-2"] and "analyze" not in roles["pc-2"]


def test_offline_quality_worker_preserves_main_review_capacity(store):
    s = store.settings()
    s.analysis_worker_id = "pc-1"
    s.quality_worker_id = "pc-2"
    store.save_settings(s)
    store.heartbeat("pc-1", ["analyze", "quality"], {"quality_revision": 1})
    store.heartbeat("pc-2", ["quality"], {"quality_revision": 1})
    store.execute("UPDATE workers SET last_seen=? WHERE id=?", (time.time() - 100, "pc-2"))
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute("UPDATE jobs SET status='processing' WHERE id=?", (job,))
    task = store.add_task(job, "quality", "q", {})
    assert store.claim("pc-1")["id"] == task


def test_capacity_uses_measured_cost_and_keeps_deleted_clips_as_historical_samples(store):
    settings = store.settings()
    settings.analysis_worker_id = "pc-1"
    store.save_settings(settings)
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    path = store.config.root / "media" / "fixture.mp4"
    path.write_bytes(b"fixture")
    asset = store.add_asset(job, path)
    clip = uid()
    now = time.time()
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,status,asset_id,created,updated) VALUES(?,?,'pc-1-g1',0,30,90,'{}','deleted',?,?,?)",
        (clip, job, asset["id"], now, now),
    )
    for kind, duration in [("analyze", 30), ("quality", 90)]:
        task = store.add_task(job, kind, kind, {})
        store.execute(
            "UPDATE tasks SET status='complete',worker_id='pc-1',started=?,finished=? WHERE id=?",
            (now - duration, now, task),
        )
        if kind == "quality":
            store.execute(
                "INSERT INTO quality_reports VALUES(?,?,?,?,?,?,?,?,?,?)",
                (task, clip, 1, asset["id"], asset["sha256"], "fixture", 0, 1, encode({}), now),
            )
    result = estimate(store)
    assert result["bottleneck_seconds_per_clip"] == 120
    assert (result["processed_low"], result["processed_high"]) == (360, 540)
    assert result["quality_scenarios"][0] == {"acceptance_percent": 25, "ready_low": 90, "ready_high": 135}
    render = store.add_task(job, "render", "slow-worker", {})
    store.execute(
        "UPDATE tasks SET status='complete',worker_id='pc-2',started=?,finished=? WHERE id=?",
        (now - 300, now, render),
    )
    result = estimate(store)
    assert result["bottleneck_worker"] == "pc-2"
    assert (result["processed_low"], result["processed_high"]) == (144, 216)

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from boti.db import Store

ALL = ["transcribe", "analyze", "render", "quality", "repair"]


@pytest.fixture
def routed(store):
    settings = store.settings()
    settings.analysis_worker_id = "pc-1"
    store.save_settings(settings)
    store.heartbeat("pc-1", ALL)
    store.heartbeat("pc-2", ALL)
    job_id, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute("UPDATE jobs SET status='processing' WHERE id=?", (job_id,))
    return store, job_id


@pytest.mark.parametrize("kind", ["analyze", "quality"])
def test_editorial_tasks_stay_on_primary_even_if_busy(routed, kind):
    store, job_id = routed
    first = store.add_task(job_id, kind, "first", {})
    store.add_task(job_id, kind, "second", {})
    assert store.claim("pc-2") is None
    assert store.claim("pc-1")["id"] == first
    assert store.claim("pc-2") is None


@pytest.mark.parametrize("kind", ["transcribe", "render", "repair"])
def test_execution_goes_to_remote_even_if_busy(routed, kind):
    store, job_id = routed
    first = store.add_task(job_id, kind, "first", {}, preferred="pc-1")
    store.add_task(job_id, kind, "second", {})
    assert store.claim("pc-1") is None
    assert store.claim("pc-2")["id"] == first
    assert store.claim("pc-1") is None


@pytest.mark.parametrize("kind", ALL)
@pytest.mark.parametrize("unavailable", ["paused", "offline", "incapable"])
def test_each_role_fails_over_when_designated_pc_cannot_do_it(routed, kind, unavailable):
    store, job_id = routed
    designated = "pc-1" if kind in {"analyze", "quality"} else "pc-2"
    fallback = "pc-2" if designated == "pc-1" else "pc-1"
    if unavailable == "paused":
        store.execute("UPDATE workers SET paused=1 WHERE id=?", (designated,))
    elif unavailable == "offline":
        store.execute("UPDATE workers SET last_seen=? WHERE id=?", (time.time() - 31, designated))
    else:
        store.heartbeat(designated, [cap for cap in ALL if cap != kind])
    task_id = store.add_task(job_id, kind, "one", {})
    assert store.claim(fallback)["id"] == task_id


def test_reconnection_restores_roles_without_revoking_active_lease(routed):
    store, job_id = routed
    store.execute("UPDATE workers SET last_seen=0 WHERE id='pc-1'")
    first = store.add_task(job_id, "analyze", "first", {})
    task = store.claim("pc-2")
    store.heartbeat("pc-1", ALL)
    store.add_task(job_id, "analyze", "second", {})
    store.finish(first, "pc-2", task["lease_token"], result={})
    assert store.claim("pc-2") is None
    assert store.claim("pc-1")["kind"] == "analyze"


def test_distributed_claims_keep_the_roles_and_do_not_duplicate_work(routed):
    store, job_id = routed
    for kind in ALL:
        store.add_task(job_id, kind, "one", {})
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = [task for task in pool.map(store.claim, ["pc-2", "pc-1"] * 4) if task]
    assert len(claimed) == 2
    assert len({task["id"] for task in claimed}) == 2
    assert all((task["worker_id"] == "pc-1") == (task["kind"] in {"analyze", "quality"}) for task in claimed)


def test_policy_persists_and_snapshot_counts_only_assigned_roles(routed):
    store, _ = routed
    store = Store(store.config)
    snapshot = store.snapshot()
    workers = {worker["id"]: worker for worker in snapshot["workers"]}
    assert set(workers["pc-1"]["assigned_capabilities"]) == {"analyze", "quality"}
    assert set(workers["pc-2"]["assigned_capabilities"]) == {"transcribe", "render", "repair"}
    assert set(workers["pc-2"]["capabilities"]) == set(ALL)
    assert all(stage["capable_workers"] == 1 for stage in snapshot["stages"])


def test_settings_validate_primary_identity_and_can_disable_policy(client, store, admin_headers):
    settings = store.settings().model_dump()
    settings["analysis_worker_id"] = "missing-pc"
    assert client.put("/api/settings", headers=admin_headers, json=settings).status_code == 422
    settings["analysis_worker_id"] = "pc-1"
    assert client.put("/api/settings", headers=admin_headers, json=settings).status_code == 200
    settings["analysis_worker_id"] = None
    assert client.put("/api/settings", headers=admin_headers, json=settings).status_code == 200
    assert store.settings().analysis_worker_id is None


def test_disabled_policy_preserves_shared_queue(routed):
    store, job_id = routed
    settings = store.settings()
    settings.analysis_worker_id = None
    store.save_settings(settings)
    store.add_task(job_id, "analyze", "one", {})
    assert store.claim("pc-2")["kind"] == "analyze"

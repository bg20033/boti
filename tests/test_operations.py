import json
import time

from boti.operations import record_download


def test_download_telemetry_preserves_metadata_and_filters_nonfinite_values(store):
    job, _ = store.create_job(
        "https://twitch.tv/videos/123", "123", ["pc-1-g1"], metadata={"reserve_bytes": 123}
    )
    record_download(
        store,
        job,
        {
            "downloaded_bytes": 100,
            "total_bytes_estimate": 200,
            "speed": float("nan"),
            "eta": -1,
            "url": "secret-url",
            "token": "secret",
        },
    )
    metadata = json.loads(store.one("SELECT metadata FROM jobs WHERE id=?", (job,))["metadata"])
    assert metadata["reserve_bytes"] == 123
    p = metadata["ingest_progress"]
    assert p["downloaded_bytes"] == 100 and p["total_bytes_estimate"] == 200
    assert not {"url", "token", "speed", "eta"} & p.keys()


def test_operations_exposes_work_ownership_and_separates_trial_metrics(client, store, admin_headers):
    now = time.time()
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute("UPDATE jobs SET status='processing',title='Real source' WHERE id=?", (job,))
    task = store.add_task(job, "transcribe", 0, {"offset": 240, "duration": 300, "secret": "never expose"})
    store.heartbeat("pc-1", ["transcribe"], {})
    claimed = store.claim("pc-1")
    assert claimed["id"] == task
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,created,updated,status) "
        "VALUES('normal',?,'pc-1-g1',0,30,90,'{}',?,?,'ready')",
        (job, now, now),
    )
    trial, _ = store.create_job(
        "https://twitch.tv/videos/456", "456", ["pc-1-g1"], trial={"duration_seconds": 180}
    )
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,created,updated,status) "
        "VALUES('trial',?,'pc-1-g1',0,30,90,'{}',?,?,'skipped')",
        (trial, now, now),
    )
    result = client.get("/api/snapshot", headers=admin_headers).json()["operations"]
    live = result["tasks"][0]
    assert live["worker_id"] == "pc-1" and live["title"] == "Real source" and live["offset"] == 240
    assert "lease_token" not in live and "secret" not in json.dumps(result)
    funnel = {row["run_mode"]: row for row in result["funnel_24h"]}
    assert funnel["production"]["accepted"] == 1 and funnel["production"]["rejected"] == 0
    assert funnel["trial"]["rejected"] == 1


def test_main_download_is_reported_as_bottleneck_instead_of_worker_failure(client, store, admin_headers):
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute("UPDATE jobs SET status='downloading' WHERE id=?", (job,))
    result = client.get("/api/snapshot", headers=admin_headers).json()["operations"]
    assert any("Main-i" in reason for reason in result["bottlenecks"])

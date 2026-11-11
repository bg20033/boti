import time

import pytest


def test_authentication_and_cookie_csrf(client, store, admin_headers):
    assert client.get("/api/snapshot").status_code == 401
    token = (store.config.root / "secrets" / "admin.token").read_text()
    result = client.post("/api/login", json={"token": token})
    assert result.status_code == 200
    csrf = result.json()["csrf"]
    assert client.get("/api/snapshot").status_code == 200
    assert client.post("/api/groups", json={"name": "Extra"}).status_code == 403
    assert (
        client.post("/api/groups", json={"name": "Extra"}, headers={"X-CSRF-Token": csrf}).status_code == 200
    )
    assert client.get("/api/snapshot").json()["settings"]["publishing_enabled"] is False


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/video",
        "https://twitch.tv.evil.test/videos/123",
        "https://twitch.tv/channel",
        "https://www.twitch.tv:443/videos/123",
        "https://x@twitch.tv/videos/123",
        "file:///tmp/x",
    ],
)
def test_vod_validation_blocks_other_hosts_and_unsupported_sources(client, admin_headers, url):
    assert client.post("/api/jobs", json={"url": url}, headers=admin_headers).status_code == 422


def test_same_vod_is_only_ingested_once(client, admin_headers):
    first = client.post(
        "/api/jobs", json={"url": "https://twitch.tv/videos/123?t=5m"}, headers=admin_headers
    ).json()
    second = client.post(
        "/api/jobs", json={"url": "https://www.twitch.tv/videos/123"}, headers=admin_headers
    ).json()
    assert first["id"] == second["id"]
    assert first["created"] and not second["created"]


def test_worker_cannot_access_another_tasks_asset_or_report_invalid_times(client, store, worker_headers):
    job_id, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute("UPDATE jobs SET status='processing' WHERE id=?", (job_id,))
    path = store.config.root / "media" / "audio.wav"
    path.write_bytes(b"test")
    asset = store.add_asset(job_id, path)
    task_id = store.add_task(job_id, "transcribe", "one", {"duration": 30}, asset_ids=[asset["id"]])
    task = client.post("/api/worker/claim", headers=worker_headers["pc-1"]).json()["task"]
    headers = {**worker_headers["pc-2"], "X-Lease-Token": task["lease_token"]}
    assert client.get(f"/api/worker/tasks/{task_id}/assets/{asset['id']}", headers=headers).status_code == 409
    response = client.post(
        f"/api/worker/tasks/{task_id}/complete",
        headers=worker_headers["pc-1"],
        json={
            "lease_token": task["lease_token"],
            "result": {"language": "sq", "segments": [{"start": 0, "end": 500, "text": "wrong"}]},
        },
    )
    assert response.status_code == 422
    store.execute("UPDATE tasks SET lease_expires=? WHERE id=?", (time.time() - 1, task_id))
    assert (
        client.post(
            "/api/worker/heartbeat",
            headers=worker_headers["pc-1"],
            json={"capabilities": ["transcribe"], "task_id": task_id, "lease_token": task["lease_token"]},
        ).status_code
        == 409
    )


def test_invalid_camera_rectangle_is_rejected(client, admin_headers):
    response = client.put(
        "/api/sources/test",
        json={"mode": "gaming", "camera": {"x": 0.9, "y": 0, "w": 0.2, "h": 0.3}},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_source_speech_preferences_round_trip(client, admin_headers):
    profile = {"mode": "gaming", "language": "sq", "vocabulary": "Prishtina, emri i lojës"}
    response = client.put("/api/sources/test-streamer", headers=admin_headers, json=profile)
    assert response.status_code == 200
    sources = client.get("/api/sources", headers=admin_headers).json()
    assert sources[0]["profile"]["language"] == "sq"
    assert sources[0]["profile"]["vocabulary"] == profile["vocabulary"]

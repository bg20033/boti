import time

import httpx
import pytest

from boti.db import encode, uid
from boti.publishing import Deferred, Publisher, check, quota_window


def publication(store, platform="youtube", group="pc-1-g1", project="shared-project"):
    job_id, _ = store.create_job("https://twitch.tv/videos/123", "123", [group])
    clip_id = uid()
    path = store.config.root / "media" / f"{clip_id}.mp4"
    path.write_bytes(b"video bytes for transfer")
    asset = store.add_asset(job_id, path)
    metadata = {"title": "A complete moment", "caption": "Original content", "hashtags": ["gaming"]}
    store.execute(
        """INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,status,asset_id,created,updated)
                     VALUES (?,?,?,0,30,90,?,'ready',?,?,?)""",
        (clip_id, job_id, group, encode(metadata), asset["id"], time.time(), time.time()),
    )
    account = store.one("SELECT * FROM accounts WHERE group_id=? AND platform=?", (group, platform))
    credentials = {
        "access_token": "secret-access-token",
        "expires_at": time.time() + 3600,
        "project_id": project,
    }
    store.execute(
        "UPDATE accounts SET credentials=?,external_id=?,status='connected' WHERE id=?",
        (
            store.config.encrypt(credentials),
            "12345" if platform == "instagram" else f"channel-{group}",
            account["id"],
        ),
    )
    pub_id = uid()
    store.execute(
        "INSERT INTO publications(id,clip_id,account_id,platform,created,updated) VALUES (?,?,?,?,?,?)",
        (pub_id, clip_id, account["id"], platform, time.time(), time.time()),
    )
    settings = store.settings()
    settings.publishing_enabled = True
    store.save_settings(settings)
    return pub_id


@pytest.mark.parametrize("platform", ["youtube", "instagram"])
def test_failed_review_holds_publication_and_preserves_upload_recovery(
    store, client, admin_headers, monkeypatch, platform
):
    pub_id = publication(store, platform=platform)
    pub = store.one("SELECT * FROM publications WHERE id=?", (pub_id,))
    state = {"session_url": "https://www.googleapis.com/upload/existing", "bytes_started": True}
    store.execute(
        "UPDATE publications SET state=?,status='processing' WHERE id=?",
        (store.config.encrypt(state), pub_id),
    )
    review = f"/api/clips/{pub['clip_id']}/review"
    response = client.post(
        review, headers=admin_headers, json={"passed": False, "notes": "Titrat kanë gabim"}
    )
    assert response.status_code == 200
    calls = []
    publisher = Publisher(
        store,
        httpx.Client(
            transport=httpx.MockTransport(lambda request: pytest.fail("Held clip made a network call"))
        ),
    )
    publisher.tick()
    persisted = store.one("SELECT * FROM publications WHERE id=?", (pub_id,))
    assert publisher.state(persisted) == state and persisted["status"] == "processing"
    clips = client.get("/api/clips", headers=admin_headers).json()["clips"]
    assert clips[0]["review_notes"] == "Titrat kanë gabim"
    assert client.post(review, headers=admin_headers, json={"passed": True}).status_code == 200

    def publish(*args):
        calls.append(args[-1])
        return {"id": "resumed"}

    monkeypatch.setattr(publisher, platform, publish)
    publisher.tick()
    assert calls == [state]
    assert store.one("SELECT status FROM publications WHERE id=?", (pub_id,))["status"] == "published"


def test_youtube_recovers_lost_completion_without_second_upload(store):
    pub_id = publication(store)
    calls = []
    finished = False

    def transport(request):
        nonlocal finished
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200, json={"items": [{"id": "recovered-id", "status": {"uploadStatus": "processed"}}]}
            )
        if request.method == "POST":
            return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/session"})
        if request.headers.get("content-range", "").startswith("bytes */"):
            return httpx.Response(200, json={"id": "recovered-id"}) if finished else httpx.Response(308)
        finished = True
        raise httpx.ReadTimeout("Response was lost", request=request)

    client = httpx.Client(transport=httpx.MockTransport(transport))
    Publisher(store, client).tick()
    row = store.one("SELECT * FROM publications WHERE id=?", (pub_id,))
    assert row["status"] == "queued"
    assert "session" not in row["state"]  # Upload URI is encrypted at rest.
    store.execute("UPDATE publications SET not_before=0 WHERE id=?", (pub_id,))
    Publisher(store, client).tick()
    row = store.one("SELECT * FROM publications WHERE id=?", (pub_id,))
    assert row["status"] == "published"
    assert row["url"].endswith("recovered-id")
    assert sum(method == "POST" for method, _ in calls) == 1


def test_youtube_project_quota_is_shared_across_accounts(store):
    publication(store, group="pc-1-g1")
    second = publication(store, group="pc-2-g1")
    settings = store.settings()
    settings.youtube_daily_uploads = 1
    store.save_settings(settings)
    starts = []

    def transport(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={"items": [{"id": "one", "status": {"uploadStatus": "processed"}}]}
            )
        if request.method == "POST":
            starts.append(request.url)
            return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/session"})
        if request.headers["content-range"].startswith("bytes */"):
            return httpx.Response(308)
        return httpx.Response(201, json={"id": "one"})

    publisher = Publisher(store, httpx.Client(transport=httpx.MockTransport(transport)))
    publisher.tick()
    publisher.tick()
    row = store.one("SELECT * FROM publications WHERE id=?", (second,))
    assert row["status"] == "queued" and row["not_before"] > time.time() + 30
    assert len(starts) == 1


def test_expired_youtube_session_needs_reconciliation(store):
    pub_id = publication(store)
    publisher = Publisher(store, httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))))
    publisher.save_state(
        pub_id, {"session_url": "https://www.googleapis.com/upload/expired", "bytes_started": True}
    )
    publisher.tick()
    assert (
        store.one("SELECT status FROM publications WHERE id=?", (pub_id,))["status"] == "needs_reconciliation"
    )


def test_instagram_recovers_lost_publish_response_with_same_container(store):
    pub_id = publication(store, "instagram")
    published = False
    container_creations, publish_calls = [], []

    def transport(request):
        nonlocal published
        if request.url.path.endswith("content_publishing_limit"):
            return httpx.Response(200, json={"data": [{"quota_usage": 0, "config": {"quota_total": 50}}]})
        if request.url.path.endswith("/media"):
            container_creations.append(1)
            return httpx.Response(
                200, json={"id": "container", "uri": "https://rupload.facebook.com/ig-api-upload/container"}
            )
        if request.url.host == "rupload.facebook.com":
            return httpx.Response(200, json={"success": True})
        if request.url.path.endswith("/media_publish"):
            published = True
            publish_calls.append(1)
            raise httpx.ReadTimeout("Lost response", request=request)
        return httpx.Response(200, json={"status_code": "PUBLISHED" if published else "FINISHED"})

    client = httpx.Client(transport=httpx.MockTransport(transport))
    publisher = Publisher(store, client)
    for _ in range(3):
        store.execute("UPDATE publications SET not_before=0 WHERE id=?", (pub_id,))
        publisher.tick()
        publisher = Publisher(store, client)
    row = store.one("SELECT * FROM publications WHERE id=?", (pub_id,))
    assert row["status"] == "published"
    assert len(container_creations) == len(publish_calls) == 1


def test_publishing_disabled_never_contacts_platforms(store):
    publication(store)
    settings = store.settings()
    settings.publishing_enabled = False
    store.save_settings(settings)

    def transport(request):
        raise AssertionError("No network call expected")

    Publisher(store, httpx.Client(transport=httpx.MockTransport(transport))).tick()


def test_quota_window_handles_pacific_daylight_saving():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime(2026, 11, 1, 12, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()
    start, reset = quota_window(now)
    assert reset - start == 25 * 3600


def test_youtube_waits_for_processing_and_does_not_reupload_rejected_video(store):
    pub_id = publication(store)
    calls = []
    processing_status = "uploaded"

    def transport(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "known-id",
                            "status": {"uploadStatus": processing_status, "rejectionReason": "duplicate"},
                        }
                    ]
                },
            )
        if request.method == "POST":
            return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/session"})
        if request.headers["content-range"].startswith("bytes */"):
            return httpx.Response(308)
        return httpx.Response(201, json={"id": "known-id"})

    publisher = Publisher(store, httpx.Client(transport=httpx.MockTransport(transport)))
    publisher.tick()
    assert store.one("SELECT status FROM publications WHERE id=?", (pub_id,))["status"] == "queued"
    processing_status = "rejected"
    store.execute("UPDATE publications SET not_before=0 WHERE id=?", (pub_id,))
    publisher.tick()
    assert store.one("SELECT status FROM publications WHERE id=?", (pub_id,))["status"] == "failed"
    assert calls.count("POST") == 1


@pytest.mark.parametrize("code", [4, 17, 32, 613, 80002])
def test_meta_throttling_with_http_400_is_deferred(code):
    with pytest.raises(Deferred):
        check(httpx.Response(400, json={"error": {"code": code, "message": "Rate limited"}}))

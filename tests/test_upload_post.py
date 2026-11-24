import json
import sqlite3

import httpx
import pytest

from boti.db import Store, encode, uid
from boti.publishing import Publisher
from boti.upload_post import PLATFORMS

URLS = {
    "youtube": "https://www.youtube.com/watch?v=published",
    "instagram": "https://www.instagram.com/reel/published/",
    "tiktok": "https://www.tiktok.com/@demo/video/123",
    "x": "https://x.com/demo/status/123",
}


def pending(store, platform):
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    path = store.config.root / "media" / "verified.mp4"
    path.write_bytes(b"verified video")
    asset = store.add_asset(job, path)
    clip, pub = uid(), uid()
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,status,asset_id,created,updated) VALUES (?,?,'pc-1-g1',0,30,90,?,'ready',?,0,0)",
        (clip, job, encode({"title": "Complete moment", "caption": "A description"}), asset["id"]),
    )
    account = store.one("SELECT * FROM accounts WHERE group_id='pc-1-g1' AND platform=?", (platform,))
    store.execute(
        "UPDATE accounts SET status='connected',external_id=?,settings=?,credentials=? WHERE id=?",
        (
            platform + "-id",
            encode({"provider": "upload_post", "daily_limit": 3, "interval_minutes": 60}),
            store.config.encrypt({"provider": "upload_post", "api_key": "private-key", "profile": "demo"}),
            account["id"],
        ),
    )
    store.execute(
        "INSERT INTO publications(id,clip_id,account_id,platform,created,updated) VALUES (?,?,?,?,0,0)",
        (pub, clip, account["id"], platform),
    )
    settings = store.settings()
    settings.publishing_enabled = True
    store.save_settings(settings)
    return pub


def transport(platform, calls, *, lost=False, fallback=False, changed=False):
    def reply(request):
        calls.append(request)
        if request.url.path.endswith("/users/demo"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "profile": {
                        "social_accounts": {
                            platform: {"username": "other-id" if changed else platform + "-id"}
                        }
                    },
                },
            )
        if request.method == "POST":
            assert request.headers["Idempotency-Key"] == request.headers["X-Request-Id"]
            assert b'name="platform[]"' in request.read()
            if lost:
                raise httpx.ReadTimeout("response lost", request=request)
            return httpx.Response(202, json={"request_id": request.headers["X-Request-Id"]})
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "results": [
                    {
                        "platform": platform,
                        "success": True,
                        "post_url": URLS[platform],
                        "fallback_to_inbox": fallback,
                    }
                ],
            },
        )

    return httpx.MockTransport(reply)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_each_platform_waits_for_confirmation_and_recovers_a_lost_response(store, platform):
    pub = pending(store, platform)
    calls = []
    publisher = Publisher(store, httpx.Client(transport=transport(platform, calls, lost=True)))
    publisher.tick()
    assert store.one("SELECT status FROM publications WHERE id=?", (pub,))["status"] != "published"
    store.execute("UPDATE publications SET not_before=0 WHERE id=?", (pub,))
    publisher.tick()
    result = store.one("SELECT status,url FROM publications WHERE id=?", (pub,))
    assert result["status"] == "published" and result["url"] == URLS[platform]
    assert sum(r.method == "POST" for r in calls) == 1
    assert "private-key" not in json.dumps(store.snapshot())


def test_tiktok_draft_is_never_reported_as_published(store):
    pub = pending(store, "tiktok")
    publisher = Publisher(store, httpx.Client(transport=transport("tiktok", [], fallback=True)))
    publisher.tick()
    store.execute("UPDATE publications SET not_before=0 WHERE id=?", (pub,))
    publisher.tick()
    assert store.one("SELECT status FROM publications WHERE id=?", (pub,))["status"] == "needs_reconciliation"


def test_changed_destination_blocks_upload_before_sending_bytes(store):
    pub = pending(store, "x")
    calls = []
    Publisher(store, httpx.Client(transport=transport("x", calls, changed=True))).tick()
    assert not any(r.method == "POST" for r in calls)
    assert (
        store.one("SELECT status FROM accounts WHERE platform='x' AND group_id='pc-1-g1'")["status"]
        == "needs_attention"
    )
    assert store.one("SELECT state FROM publications WHERE id=?", (pub,))["state"] == "{}"


def test_provider_connection_verifies_all_selected_accounts_and_encrypts_key(
    store, client, admin_headers, monkeypatch
):
    original = httpx.Client

    def reply(request):
        return httpx.Response(
            200,
            json={
                "success": True,
                "profile": {
                    "social_accounts": {p: {"username": p + "-id", "handle": "@demo"} for p in PLATFORMS}
                },
            },
        )

    monkeypatch.setattr(
        "boti.upload_post.httpx.Client", lambda **kwargs: original(transport=httpx.MockTransport(reply))
    )
    body = {"api_key": "secret-provider-key", "profile": "demo"}
    assert client.post("/api/groups/pc-1-g1/upload-post", json=body).status_code == 401
    response = client.post("/api/groups/pc-1-g1/upload-post", headers=admin_headers, json=body)
    assert response.status_code == 200 and len(response.json()["connected"]) == 4
    accounts = store.all("SELECT * FROM accounts WHERE group_id='pc-1-g1'")
    assert all(a["status"] == "connected" for a in accounts)
    assert all("secret-provider-key" not in a["credentials"] for a in accounts)
    assert "secret-provider-key" not in json.dumps(store.snapshot())
    assert store.settings().publishing_enabled is False


def test_x_migration_preserves_existing_accounts_publications_and_foreign_keys(store):
    pub = pending(store, "youtube")
    ids = {a["id"] for a in store.all("SELECT id FROM accounts WHERE platform!='x'")}
    # Reconstruct the previous schema, retaining a real publication referencing its account.
    with sqlite3.connect(store.config.db_path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='accounts'").fetchone()[0]
        db.execute(
            sql.replace("CREATE TABLE accounts", "CREATE TABLE accounts_legacy", 1).replace(",'x'", "")
        )
        db.execute("INSERT INTO accounts_legacy SELECT * FROM accounts WHERE platform!='x'")
        db.execute("DROP TABLE accounts")
        db.execute("ALTER TABLE accounts_legacy RENAME TO accounts")
    upgraded = Store(store.config)
    upgraded.seed()
    assert ids <= {a["id"] for a in upgraded.all("SELECT id FROM accounts")}
    assert upgraded.one("SELECT id FROM publications WHERE id=?", (pub,))
    assert not upgraded.all("PRAGMA foreign_key_check")

import json
import time

from boti.campaigns import publish_allowed
from boti.db import Store, uid


def create(client, headers, **kwargs):
    response = client.post(
        "/api/campaigns", headers=headers, json={"name": "Kick test", "group_ids": ["pc-1-g1"], **kwargs}
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_campaign_validation_and_auth(client, admin_headers):
    assert client.get("/api/campaigns").status_code == 401
    for changes in (
        {"url": "https://evil.test"},
        {"rate_unit": 1},
        {"group_ids": ["missing"]},
        {"rate": "-1"},
        {"rules_confirmed": True},
        {"group_ids": ["pc-1-g1", "pc-2-g1"], "max_accounts_per_platform": 1},
    ):
        assert (
            client.post("/api/campaigns", headers=admin_headers, json={"name": "test", **changes}).status_code
            == 422
        )


def test_campaign_snapshot_destination_and_restart(client, store, admin_headers):
    identity = create(client, admin_headers, rate="0.123456")
    request = {"url": "https://twitch.tv/videos/9123", "campaign_id": identity, "group_ids": ["pc-2-g1"]}
    assert client.post("/api/jobs", headers=admin_headers, json=request).status_code == 422
    request["group_ids"] = ["pc-1-g1"]
    response = client.post("/api/jobs", headers=admin_headers, json=request)
    assert response.status_code == 200
    job = response.json()["id"]
    assert client.post("/api/jobs", headers=admin_headers, json=request).json()["created"] is False
    request["campaign_id"] = None
    assert client.post("/api/jobs", headers=admin_headers, json=request).status_code == 422
    reopened = Store(store.config)
    snapshot = reopened.one("SELECT * FROM job_campaigns WHERE job_id=?", (job,))
    assert json.loads(snapshot["rules_snapshot"])["rate"] == "0.123456"


def test_campaign_changes_require_explicit_recheck_and_new_clip_review(client, store, admin_headers):
    identity = create(
        client,
        admin_headers,
        status="active",
        rules="Real rules",
        rules_confirmed=True,
        automation_confirmed=True,
    )
    job, _ = store.create_job("https://twitch.tv/videos/1", "1", ["pc-1-g1"], identity)
    clip = {"id": uid(), "job_id": job, "group_id": "pc-1-g1"}
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,created,updated) VALUES (?,?,?,0,30,90,'{}',?,?)",
        (clip["id"], job, clip["group_id"], time.time(), time.time()),
    )
    assert not publish_allowed(store, clip)
    store.execute("INSERT INTO reviews VALUES (?,1,?,?)", (clip["id"], "", time.time()))
    assert publish_allowed(store, clip)
    data = client.get("/api/campaigns", headers=admin_headers).json()[0]["data"]
    data["rules"] = "Updated rules"
    assert client.put("/api/campaigns/" + identity, headers=admin_headers, json=data).status_code == 200
    assert not publish_allowed(store, clip)
    assert client.post("/api/jobs/" + job + "/campaign-review", headers=admin_headers).status_code == 200
    assert not publish_allowed(store, clip)
    assert not store.one("SELECT * FROM reviews WHERE clip_id=?", (clip["id"],))


def test_submission_is_manual_idempotent_and_preserved(client, store, admin_headers):
    identity = create(client, admin_headers)
    job, _ = store.create_job("https://twitch.tv/videos/1", "1", ["pc-1-g1"], identity)
    clip, pub = uid(), uid()
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,created,updated) VALUES (?,?,'pc-1-g1',0,30,90,?,0,0)",
        (clip, job, json.dumps({"title": "Test clip"})),
    )
    account = store.one("SELECT id FROM accounts WHERE group_id='pc-1-g1' AND platform='youtube'")["id"]
    store.execute(
        "INSERT INTO publications(id,clip_id,account_id,platform,status,url,created,updated) VALUES (?,?,?,'youtube','published','https://youtube.com/shorts/test',0,0)",
        (pub, clip, account),
    )
    rows = client.get("/api/submissions", headers=admin_headers).json()
    assert rows[0]["status"] == "ready"
    assert (
        client.put("/api/submissions/" + pub, headers=admin_headers, json={"status": "tracking"}).status_code
        == 422
    )
    data = {"status": "tracking", "reference": "Confirmed in dashboard"}
    for _ in range(2):
        assert client.put("/api/submissions/" + pub, headers=admin_headers, json=data).status_code == 200
    assert store.one("SELECT count(*) n FROM submission_history")["n"] == 1
    assert Store(store.config).one("SELECT status FROM submissions")["status"] == "tracking"

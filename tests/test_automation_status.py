import json

from boti.db import encode


def snapshot(client, headers):
    response = client.get("/api/snapshot", headers=headers)
    assert response.status_code == 200
    return response.json()["automation"]


def test_no_feeds_cannot_look_ready_and_status_is_read_only(client, store, admin_headers):
    before = store.settings().model_dump()
    result = snapshot(client, admin_headers)
    assert not result["configured"]
    assert not any(p["ready"] for p in result["platforms"])
    assert store.settings().model_dump() == before


def test_trial_and_manual_accounts_cannot_look_fully_automatic(client, store, admin_headers):
    response = client.post(
        "/api/feeds",
        headers=admin_headers,
        json={"channel": "xqc", "group_ids": ["pc-1-g1"], "trial": {"duration_seconds": 180}},
    )
    assert response.status_code == 200
    store.execute(
        "UPDATE accounts SET status='connected',credentials='encrypted' WHERE platform='youtube' AND group_id='pc-1-g1'"
    )
    result = snapshot(client, admin_headers)
    assert not result["configured"]
    assert next(p for p in result["platforms"] if p["platform"] == "youtube")["ready"]
    assert not next(c for c in result["checks"] if c["id"] == "feeds")["ready"]
    feed = store.one("SELECT data FROM feeds")
    assert json.loads(feed["data"])["trial"] is not None


def test_readiness_checks_all_destination_groups_and_campaign_permissions(client, store, admin_headers):
    response = client.post(
        "/api/feeds", headers=admin_headers, json={"channel": "xqc", "group_ids": ["pc-1-g1", "pc-2-g1"]}
    )
    assert response.status_code == 200
    store.execute("UPDATE accounts SET status='connected',credentials='encrypted' WHERE group_id='pc-1-g1'")
    result = snapshot(client, admin_headers)
    assert all(p["missing_groups"] == ["pc-2-g1"] for p in result["platforms"])
    assert result["campaign_issues"] == []  # Campaigns are optional for ordinary posting.
    assert next(c for c in result["checks"] if c["id"] == "feeds")["ready"]
    assert not result["configured"]
    campaign = {
        "name": "Confirmed source",
        "group_ids": ["pc-1-g1", "pc-2-g1"],
        "status": "active",
        "rules": "Use complete clips from xqc.",
        "rules_confirmed": True,
        "automation_confirmed": True,
    }
    created = client.post("/api/campaigns", headers=admin_headers, json=campaign)
    assert created.status_code == 200, created.text
    feed = store.one("SELECT * FROM feeds")
    data = json.loads(feed["data"])
    data["campaign_id"] = created.json()["id"]
    store.execute("UPDATE feeds SET data=?", (encode(data),))
    store.execute("UPDATE accounts SET status='connected',credentials='encrypted' WHERE group_id='pc-2-g1'")
    # Configuration can be ready while individual clips still require QA; status never posts anything.
    for worker in ("pc-1", "pc-2"):
        store.heartbeat(
            worker,
            ["transcribe", "analyze", "render", "quality", "repair"],
            {"render_revision": 2, "archive_protocol": 1},
        )
    settings = store.settings()
    settings.publishing_enabled = True
    store.save_settings(settings)
    result = snapshot(client, admin_headers)
    assert result["campaign_issues"] == []
    assert result["configured"]
    assert result["outbox"] == []
    campaign["automation_confirmed"] = False
    updated = client.put(f"/api/campaigns/{created.json()['id']}", headers=admin_headers, json=campaign)
    assert updated.status_code == 200, updated.text
    result = snapshot(client, admin_headers)
    assert not result["configured"] and result["campaign_issues"][0]["channel"] == "xqc"


def test_posting_without_campaign_can_be_configured(client, store, admin_headers):
    response = client.post(
        "/api/feeds", headers=admin_headers, json={"channel": "xqc", "group_ids": ["pc-1-g1"]}
    )
    assert response.status_code == 200
    store.execute("UPDATE accounts SET status='connected',credentials='encrypted' WHERE group_id='pc-1-g1'")
    store.heartbeat(
        "pc-1",
        ["transcribe", "analyze", "render", "quality", "repair"],
        {"render_revision": 2, "archive_protocol": 1},
    )
    settings = store.settings()
    settings.publishing_enabled = True
    store.save_settings(settings)
    assert snapshot(client, admin_headers)["configured"]
    assert store.one("SELECT count(*) n FROM campaigns")["n"] == 0

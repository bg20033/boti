import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from boti.cli import connect_youtube


@pytest.fixture
def oauth_client(tmp_path, monkeypatch):
    client_file = tmp_path / "client.json"
    client_file.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "project_id": "project",
                }
            }
        )
    )
    calls = []

    def authorize(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            token="private-access",
            refresh_token="private-refresh",
            expiry=datetime.now(UTC) + timedelta(hours=1),
        )

    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        lambda *args, **kwargs: SimpleNamespace(run_local_server=authorize),
    )
    return client_file, calls


def mock_google(monkeypatch, items):
    original = httpx.Client
    requests = []

    def reply(request):
        requests.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "refreshed-access", "expires_in": 3600})
        return httpx.Response(200, json={"items": items})

    monkeypatch.setattr(
        "boti.cli.httpx.Client", lambda **kwargs: original(transport=httpx.MockTransport(reply))
    )
    return requests


def test_missing_channel_preserves_consent_encrypted_and_resume_needs_no_second_consent(
    store, oauth_client, monkeypatch
):
    path, authorizations = oauth_client
    channels = []
    mock_google(monkeypatch, channels)
    with pytest.raises(ValueError, match="nuk ka kanal YouTube"):
        connect_youtube(store, "pc-1-g1", path, "private", no_browser=True)
    pending = list((store.config.root / "secrets").glob("youtube-pending-*.enc"))
    assert len(pending) == 1 and "private-refresh" not in pending[0].read_text()
    assert (
        store.one("SELECT status FROM accounts WHERE group_id='pc-1-g1' AND platform='youtube'")["status"]
        == "disconnected"
    )
    channels.append({"id": "channel-id", "snippet": {"title": "My channel"}})
    connect_youtube(store, "pc-1-g1", path, "private", no_browser=True)
    assert len(authorizations) == 1
    account = store.one("SELECT * FROM accounts WHERE group_id='pc-1-g1' AND platform='youtube'")
    assert account["status"] == "connected" and account["name"] == "My channel"
    assert json.loads(account["settings"])["privacy"] == "private"
    assert store.config.decrypt(account["credentials"])["refresh_token"] == "private-refresh"
    assert not pending[0].exists()


def test_multiple_channels_require_explicit_owned_choice_and_pending_token_can_refresh(
    store, oauth_client, monkeypatch
):
    path, authorizations = oauth_client
    requests = mock_google(monkeypatch, [{"id": x, "snippet": {"title": x}} for x in ("one", "two")])
    with pytest.raises(ValueError, match="--channel"):
        connect_youtube(store, "pc-1-g1", path, "private", no_browser=True)
    pending = next((store.config.root / "secrets").glob("youtube-pending-*.enc"))
    secret = store.config.decrypt(pending.read_text())
    secret["expires_at"] = time.time() - 1
    pending.write_text(store.config.encrypt(secret))
    with pytest.raises(ValueError, match="--channel"):
        connect_youtube(store, "pc-1-g1", path, "private", no_browser=True, channel_id="not-owned")
    connect_youtube(store, "pc-1-g1", path, "private", no_browser=True, channel_id="two")
    assert len(authorizations) == 1
    assert any(r.url.path.endswith("/token") for r in requests)
    account = store.one("SELECT * FROM accounts WHERE group_id='pc-1-g1' AND platform='youtube'")
    assert account["external_id"] == "two"
    assert store.config.decrypt(account["credentials"])["access_token"] == "refreshed-access"

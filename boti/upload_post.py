"""Opt-in Upload-Post integration. Connections and uploads never imply a successful public post."""

import json
import time
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from boti.db import encode

BASE = "https://api.upload-post.com"
PLATFORMS = ("tiktok", "instagram", "youtube", "x")


def migrate(db):
    sql = db.execute("SELECT sql FROM sqlite_master WHERE name='accounts'").fetchone()[0]
    if "'x'" in sql:
        return
    # No parent-table rename: publications keep their existing REFERENCES accounts(id).
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute("BEGIN IMMEDIATE")
    try:
        from boti.db import SCHEMA

        columns = SCHEMA.split("CREATE TABLE IF NOT EXISTS accounts (", 1)[1].split(");", 1)[0]
        db.execute("CREATE TABLE accounts_next (" + columns + ");")
        db.execute("INSERT INTO accounts_next SELECT * FROM accounts")
        db.execute("DROP TABLE accounts")
        db.execute("ALTER TABLE accounts_next RENAME TO accounts")
        db.execute(
            "CREATE UNIQUE INDEX account_destination ON accounts(platform,external_id) WHERE external_id IS NOT NULL"
        )
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Account migration would break existing references")
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.execute("PRAGMA foreign_keys=ON")


def profile(client, key, username):
    response = client.get(
        f"{BASE}/api/uploadposts/users/{quote(username, safe='')}", headers={"Authorization": f"Apikey {key}"}
    )
    response.raise_for_status()
    value = response.json()
    result = value.get("profile")
    if value.get("success") is not True or not isinstance(result, dict):
        raise ValueError("Upload-Post nuk e verifikoi profilin")
    return result


class Connection(BaseModel):
    api_key: str = Field(min_length=10, max_length=2000)
    profile: str = Field(min_length=1, max_length=100, pattern=r"^[\w.-]+$")
    platforms: list[str] = Field(default_factory=lambda: list(PLATFORMS), min_length=1, max_length=4)
    daily_limit: int = Field(default=3, ge=1, le=100)
    interval_minutes: int = Field(default=60, ge=1, le=1440)
    youtube_privacy: str = Field(default="private", pattern="^(private|unlisted|public)$")
    tiktok_privacy: str = Field(
        default="SELF_ONLY",
        pattern="^(SELF_ONLY|PUBLIC_TO_EVERYONE|MUTUAL_FOLLOW_FRIENDS|FOLLOWER_OF_CREATOR)$",
    )


def routes(store, admin):
    router = APIRouter(prefix="/api", dependencies=[Depends(admin)])

    @router.post("/groups/{group_id}/upload-post")
    def connect(group_id: str, body: Connection):
        if not store.one("SELECT 1 FROM groups WHERE id=?", (group_id,)):
            raise HTTPException(404)
        if any(p not in PLATFORMS for p in body.platforms):
            raise HTTPException(422, "Platformë e panjohur")
        try:
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                found = profile(client, body.api_key, body.profile)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(
                422,
                "Profili nuk u verifikua. Kontrollo API key, emrin e profilit dhe lidhjet në Upload-Post.",
            ) from exc
        socials = found.get("social_accounts") or {}
        missing = [p for p in body.platforms if not (socials.get(p) or {}).get("username")]
        if missing:
            raise HTTPException(422, "Lidhi së pari në Upload-Post: " + ", ".join(missing))
        with store.connect(immediate=True) as db:
            for platform in dict.fromkeys(body.platforms):
                account = db.execute(
                    "SELECT * FROM accounts WHERE group_id=? AND platform=?", (group_id, platform)
                ).fetchone()
                social = socials[platform]
                external = str(social["username"])
                if db.execute(
                    "SELECT 1 FROM accounts WHERE platform=? AND external_id=? AND id!=?",
                    (platform, external, account["id"]),
                ).fetchone():
                    raise HTTPException(409, "Llogaria është në një grup tjetër: " + platform)
                if db.execute(
                    "SELECT 1 FROM publications WHERE account_id=? AND status IN ('processing','needs_reconciliation')",
                    (account["id"],),
                ).fetchone():
                    raise HTTPException(409, "Përfundo verifikimin e publikimit aktiv para rilidhjes")
                credentials = {"provider": "upload_post", "api_key": body.api_key, "profile": body.profile}
                settings = {
                    **json.loads(account["settings"]),
                    "provider": "upload_post",
                    "daily_limit": body.daily_limit,
                    "interval_minutes": body.interval_minutes,
                    "youtube_privacy": body.youtube_privacy,
                    "tiktok_privacy": body.tiktok_privacy,
                }
                db.execute(
                    "UPDATE accounts SET external_id=?,name=?,credentials=?,settings=?,status='connected',error=NULL WHERE id=?",
                    (
                        external,
                        str(social.get("handle") or social.get("display_name") or external)[:100],
                        store.config.encrypt(credentials),
                        encode(settings),
                        account["id"],
                    ),
                )
                # Only legacy manual entries with no transfer started can enter the automated outbox.
                db.execute(
                    "UPDATE publications SET status='queued' WHERE account_id=? AND status='manual_ready' AND state='{}'",
                    (account["id"],),
                )
        return {"connected": list(dict.fromkeys(body.platforms)), "profile": body.profile}

    return router


def publish(publisher, publication, account, credentials, path, metadata, state):
    from boti.publishing import Ambiguous, Deferred, Reconnect

    publisher.check_quality(publication)
    settings = json.loads(account["settings"])
    key, username, platform = credentials["api_key"], credentials["profile"], publication["platform"]
    headers = {"Authorization": f"Apikey {key}"}
    operation = state.get("upload_post")
    if operation:
        if operation["profile"] != username or operation["destination"] != account["external_id"]:
            raise Reconnect("Destinacioni ndryshoi gjatë publikimit; verifiko postimin ekzistues")
        response = publisher.client.get(
            f"{BASE}/api/uploadposts/status", params={"request_id": operation["request_id"]}, headers=headers
        )
        if response.status_code == 404:
            raise Ambiguous("Upload-Post nuk konfirmoi kërkesën e nisur; kontrollo historinë para riprovimit")
        if response.status_code == 401:
            raise Reconnect("API key i Upload-Post skadoi; rilidhe profilin")
        if response.status_code == 429 or response.status_code >= 500:
            raise Deferred("Upload-Post është përkohësisht i zënë", 120)
        response.raise_for_status()
        value = response.json()
        results = value.get("results") or []
        if isinstance(results, dict):
            result = results.get(platform) or {}
        else:
            result = next((r for r in results if r.get("platform") == platform), {})
        if result.get("fallback_to_inbox"):
            raise Ambiguous(
                "TikTok e dërgoi në inbox/drafts; nuk është postim publik. Përfundoje në TikTok dhe regjistro linkun."
            )
        if result.get("skipped") or result.get("status") == "skipped":
            raise Reconnect("Platforma u shkëput nga Upload-Post; rilidhe llogarinë")
        if (
            result.get("success") is False
            or result.get("status") in {"failed", "retryable"}
            or value.get("status") == "failed"
        ):
            raise Ambiguous("Shërbimi raportoi dështim; kontrollo profilin dhe historinë para ripublikimit")
        url = result.get("post_url") or result.get("url")
        if result.get("success") is True and url:
            allowed = {
                "youtube": {"youtube.com", "www.youtube.com", "youtu.be"},
                "instagram": {"instagram.com", "www.instagram.com"},
                "tiktok": {"tiktok.com", "www.tiktok.com"},
                "x": {"x.com", "www.x.com", "twitter.com", "www.twitter.com"},
            }[platform]
            parsed = urlparse(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in allowed
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
            ):
                raise Ambiguous("Rezultati nuk përmban link të verifikueshëm të platformës")
            return {"id": result.get("platform_post_id") or result.get("post_id"), "url": url}
        if time.time() - operation["started_at"] > 86400:
            raise Ambiguous("Nuk u mor linku i postimit brenda 24 orëve; kontrollo historinë")
        raise Deferred("Platforma po përpunon videon; presim konfirmimin dhe linkun", 30)

    # Check the exact connected destination before accepting a new transfer.
    try:
        found = profile(publisher.client, key, username)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403, 404):
            raise Reconnect("Upload-Post nuk e verifikoi profilin; kontrollo çelësin dhe rilidhe") from exc
        raise Deferred("Verifikimi i profilit Upload-Post pret përgjigjen e shërbimit", 120) from exc
    social = (found.get("social_accounts") or {}).get(platform) or {}
    if str(social.get("username", "")) != account["external_id"]:
        raise Reconnect("Llogaria në profilin Upload-Post ndryshoi; lidhe dhe verifikoje përsëri")
    text = metadata.get("post_text") or metadata.get("caption") or metadata["title"]
    from boti.rules import PLATFORM_CAPTION_LIMIT

    if len(text) > PLATFORM_CAPTION_LIMIT[platform]:
        raise ValueError("Teksti i detyrueshëm tejkalon kufirin e platformës; rregullo rregullat e fushatës")
    state["upload_post"] = {
        "request_id": publication["id"],
        "profile": username,
        "destination": account["external_id"],
        "started_at": time.time(),
    }
    publisher.save_state(publication["id"], state)
    headers.update({"Idempotency-Key": publication["id"], "X-Request-Id": publication["id"]})
    data = {
        "user": username,
        "platform[]": platform,
        "async_upload": "true",
        "request_id": publication["id"],
        "external_id": publication["clip_id"],
        "title": metadata["title"][:100] if platform == "youtube" else text,
        "description": text,
        "post_mode": "DIRECT_POST",
    }
    if platform == "youtube":
        data["privacyStatus"] = settings.get("youtube_privacy", "private")
    if platform == "tiktok":
        data["privacy_level"] = settings.get("tiktok_privacy", "SELF_ONLY")
    with path.open("rb") as handle:
        response = publisher.client.post(
            f"{BASE}/api/upload",
            headers=headers,
            data=data,
            files={"video": ("video.mp4", handle, "video/mp4")},
        )
    if response.status_code == 401:
        raise Reconnect("Upload-Post nuk e pranoi API key")
    # The request may already have reached the provider, including on a 5xx. Poll its stable ID next.
    if not response.is_success:
        raise Deferred("Transferimi kërkon verifikim te Upload-Post", 30)
    return None

"""Persistent channel discovery feeds the existing bounded processing queue."""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from itertools import islice
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from boti.campaigns import Campaign
from boti.db import uid
from boti.models import TrialOptions
from boti.rules import source_allowed
from boti.sources import validate_info, vod_url

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
 id TEXT PRIMARY KEY, channel TEXT NOT NULL UNIQUE, data TEXT NOT NULL,
 initialized INTEGER NOT NULL DEFAULT 0, next_check REAL NOT NULL DEFAULT 0,
 checked_at REAL, error TEXT, last_job_id TEXT,
 created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_items (
 feed_id TEXT NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
 video_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, published REAL NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', job_id TEXT REFERENCES jobs(id),
 PRIMARY KEY(feed_id,video_id)
);
"""


class FeedConfig(BaseModel):
    channel: str = Field(min_length=1, max_length=200)
    group_ids: list[str] = Field(min_length=1, max_length=500)
    campaign_id: str | None = None
    enabled: bool = True
    include_existing: bool = True
    interval_seconds: int = Field(default=300, ge=60, le=86400)
    pending_limit: int = Field(default=2, ge=1, le=20)
    trial: TrialOptions | None = None

    @field_validator("channel")
    @classmethod
    def kick_channel(cls, value):
        value = value.strip()
        if "://" in value:
            parsed = urlparse(value)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in {"kick.com", "www.kick.com"}
                or parsed.username
                or parsed.password
                or parsed.port
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Jep emrin ose linkun HTTPS të kanalit Kick")
            value = parsed.path.strip("/")
            if value.endswith("/videos"):
                value = value[:-7]
        value = value.removeprefix("@").lower()
        if not re.fullmatch(r"[a-z0-9_\-]{1,60}", value):
            raise ValueError("Jep emrin e saktë të kanalit Kick")
        return value

    @field_validator("group_ids")
    @classmethod
    def unique_groups(cls, values):
        return list(dict.fromkeys(values))


def validate_destination(store, config):
    groups = store.all("SELECT id FROM groups WHERE paused=0")
    if not set(config.group_ids).issubset({g["id"] for g in groups}):
        raise ValueError("Zgjidh grupe ekzistuese që janë aktive")
    if config.campaign_id:
        row = store.one("SELECT data FROM campaigns WHERE id=?", (config.campaign_id,))
        if not row:
            raise ValueError("Fushata nuk ekziston")
        campaign = Campaign.model_validate_json(row["data"])
        if campaign.status in {"paused", "closed"} or (
            campaign.deadline and campaign.deadline <= time.time()
        ):
            raise ValueError("Fushata është pezulluar, mbyllur ose skaduar")
        if not set(config.group_ids).issubset(campaign.group_ids):
            raise ValueError("Grupet duhet t'i përkasin fushatës")
        if not source_allowed(campaign.clip, config.channel, f"https://kick.com/{config.channel}"):
            raise ValueError("Kanali nuk lejohet nga rregullat e fushatës")


def list_feeds(store):
    rows = store.all("""
        SELECT f.*,
        (SELECT count(*) FROM feed_items i WHERE i.feed_id=f.id AND i.state='pending') pending,
        (SELECT count(*) FROM feed_items i WHERE i.feed_id=f.id AND i.state='imported') imported
        FROM feeds f ORDER BY f.created,f.id
    """)
    for row in rows:
        row["data"] = json.loads(row["data"])
    return rows


def discover(channel):
    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "lazy_playlist": True,
        "playlistend": 50,
        "socket_timeout": 15,
        "extractor_retries": 1,
    }
    result = []
    with YoutubeDL(options) as downloader:
        playlist = downloader.extract_info(f"https://kick.com/{channel}/videos", download=False)
        if not isinstance(playlist, dict) or "entries" not in playlist:
            raise ValueError("Kick nuk ktheu listën e videove të kanalit")
        for entry in islice(playlist["entries"], 50):
            if not entry:
                continue
            info = {k: v for k, v in entry.items() if k != "_type"}
            try:
                validate_info(info)
                url, provider, video_id = vod_url(entry["url"])
            except (KeyError, ValueError):
                continue
            if provider != "kick" or not url.startswith(f"https://kick.com/{channel}/videos/"):
                continue
            result.append(
                {
                    "id": video_id,
                    "url": url,
                    "title": str(entry.get("title") or video_id)[:500],
                    "published": float(entry.get("timestamp") or 0),
                }
            )
    return sorted(result, key=lambda item: item["published"], reverse=True)


class FeedWatcher:
    def __init__(self, store, discover_videos=discover):
        self.store = store
        self.discover = discover_videos

    def tick(self):
        store = self.store
        settings = store.settings()
        if settings.paused or shutil.disk_usage(store.config.root).free < settings.min_free_gb * 1024**3:
            return
        for row in store.all("""
            SELECT f.id,f.data FROM feeds f LEFT JOIN jobs j ON j.id=f.last_job_id
            ORDER BY coalesce(j.created,0),f.created,f.id
        """):
            if FeedConfig.model_validate_json(row["data"]).enabled:
                try:
                    self.enqueue(row["id"])
                except Exception as exc:
                    store.execute("UPDATE feeds SET error=? WHERE id=?", (str(exc)[:1000], row["id"]))
        for row in store.all(
            "SELECT * FROM feeds WHERE next_check<=? ORDER BY next_check LIMIT 10", (time.time(),)
        ):
            config = FeedConfig.model_validate_json(row["data"])
            if not config.enabled:
                continue
            if not store.execute(
                "UPDATE feeds SET next_check=? WHERE id=? AND next_check=?",
                (time.time() + config.interval_seconds, row["id"], row["next_check"]),
            ):
                continue
            try:
                validate_destination(store, config)
                videos = self.discover(config.channel)
                with store.connect(immediate=True) as db:
                    current = db.execute("SELECT data FROM feeds WHERE id=?", (row["id"],)).fetchone()
                    if not current or current["data"] != row["data"]:
                        continue
                    for video in videos:
                        state = "pending" if row["initialized"] or config.include_existing else "ignored"
                        db.execute(
                            "INSERT OR IGNORE INTO feed_items VALUES (?,?,?,?,?,?,NULL)",
                            (row["id"], video["id"], video["url"], video["title"], video["published"], state),
                        )
                    db.execute(
                        "UPDATE feeds SET initialized=1,checked_at=?,error=NULL WHERE id=?",
                        (time.time(), row["id"]),
                    )
                self.enqueue(row["id"])
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"[:1000]
                store.execute(
                    "UPDATE feeds SET checked_at=?,error=? WHERE id=?", (time.time(), message, row["id"])
                )
                log.warning("Kontrolli i kanalit %s dështoi: %s", config.channel, message)

    def enqueue(self, feed_id):
        store = self.store
        for item in store.all(
            "SELECT * FROM feed_items WHERE feed_id=? AND state='pending' ORDER BY published DESC LIMIT 20",
            (feed_id,),
        ):
            row = store.one("SELECT * FROM feeds WHERE id=?", (feed_id,))
            if not row:
                return
            config = FeedConfig.model_validate_json(row["data"])
            if not config.enabled or store.settings().paused:
                return
            validate_destination(store, config)
            pending = store.one(
                "SELECT count(*) n FROM jobs WHERE status IN ('queued','downloading','processing')"
            )
            if pending["n"] >= config.pending_limit:
                return
            job_id, created = store.create_job(
                item["url"],
                item["video_id"],
                config.group_ids,
                campaign_id=config.campaign_id,
                provider="kick",
                review_mode="automatic",
                trial=config.trial.model_dump() if config.trial else None,
            )
            store.execute(
                "UPDATE feed_items SET state='imported',job_id=? WHERE feed_id=? AND video_id=?",
                (job_id, feed_id, item["video_id"]),
            )
            store.execute("UPDATE feeds SET last_job_id=? WHERE id=?", (job_id, feed_id))
            if created:
                store.event(f"Automatikisht nga {config.channel}: {item['title']}", job_id=job_id)


def routes(store, admin):
    router = APIRouter(prefix="/api/feeds", dependencies=[Depends(admin)])

    @router.get("")
    def feeds():
        return list_feeds(store)

    @router.post("")
    def create(body: FeedConfig):
        try:
            validate_destination(store, body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        identity, now = uid(), time.time()
        with store.connect(immediate=True) as db:
            if db.execute("SELECT id FROM feeds WHERE channel=?", (body.channel,)).fetchone():
                raise HTTPException(409, "Ky kanal po ndiqet tashmë")
            db.execute(
                "INSERT INTO feeds(id,channel,data,created,updated) VALUES (?,?,?,?,?)",
                (identity, body.channel, body.model_dump_json(), now, now),
            )
        return {"id": identity}

    @router.put("/{identity}")
    def update(identity: str, body: FeedConfig):
        row = store.one("SELECT channel FROM feeds WHERE id=?", (identity,))
        if not row:
            raise HTTPException(404, "Kanali nuk gjendet")
        if body.channel != row["channel"]:
            raise HTTPException(422, "Për kanal tjetër shto një ndjekje të re")
        if body.enabled:
            try:
                validate_destination(store, body)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        store.execute(
            "UPDATE feeds SET data=?,updated=?,next_check=0,error=NULL WHERE id=?",
            (body.model_dump_json(), time.time(), identity),
        )
        return {"ok": True}

    @router.post("/{identity}/check")
    def check(identity: str):
        if not store.execute("UPDATE feeds SET next_check=0 WHERE id=?", (identity,)):
            raise HTTPException(404, "Kanali nuk gjendet")
        return {"ok": True}

    @router.delete("/{identity}")
    def remove(identity: str):
        if not store.execute("DELETE FROM feeds WHERE id=?", (identity,)):
            raise HTTPException(404, "Kanali nuk gjendet")
        return {"ok": True}

    return router

"""Strict completed-video adapters. Provider errors are terminal until an explicit retry."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".ts", ".flv"}


class TwitchVOD:
    provider = "twitch"
    hosts = {"twitch.tv", "www.twitch.tv"}
    pattern = re.compile(r"/videos/(?P<id>\d+)/?")
    extractor = "TwitchVod"
    format = "best[height<=1080]/best"

    @staticmethod
    def canonical(match):
        return f"https://www.twitch.tv/videos/{match['id']}"


class KickVOD:
    provider = "kick"
    hosts = {"kick.com", "www.kick.com"}
    pattern = re.compile(
        r"/(?P<channel>[\w-]+)/videos/(?P<id>[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12})/?", re.I
    )
    extractor = "KickVOD"
    format = "best[height<=1080]/best"

    @staticmethod
    def canonical(match):
        return f"https://kick.com/{match['channel'].lower()}/videos/{match['id'].lower()}"


class YouTubeVideo:
    """Finished long-form uploads and ended streams. Shorts, playlists and channel pages are refused."""

    provider = "youtube"
    hosts = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
    pattern = re.compile(r"/(?:watch|live/(?P<live>[\w-]{11})|(?P<short>[\w-]{11}))/?")
    extractor = "Youtube"
    # H.264 + AAC first: no transcode before cutting. Separate streams are merged by FFmpeg.
    format = (
        "bestvideo[height<=1080][vcodec^=avc1]+bestaudio[ext=m4a]/"
        "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
    )

    @staticmethod
    def canonical(match):
        return f"https://www.youtube.com/watch?v={match['id']}"


ADAPTERS = {a.provider: a for a in (TwitchVOD, KickVOD, YouTubeVideo)}


def _youtube_id(parsed):
    match = YouTubeVideo.pattern.fullmatch(parsed.path)
    if not match:
        return None
    if parsed.hostname == "youtu.be":
        return match["short"]
    if match["live"]:
        return match["live"]
    if match["short"]:
        return None
    video = parse_qs(parsed.query).get("v", [""])[0]
    return video if re.fullmatch(r"[\w-]{11}", video) else None


def vod_url(value):
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        raise ValueError("Përdor link HTTPS të një videoje Kick, Twitch ose YouTube")
    if parsed.hostname in YouTubeVideo.hosts:
        video = _youtube_id(parsed)
        if video:
            return YouTubeVideo.canonical({"id": video}), "youtube", video
    for adapter in (TwitchVOD, KickVOD):
        match = adapter.pattern.fullmatch(parsed.path) if parsed.hostname in adapter.hosts else None
        if match:
            return adapter.canonical(match), adapter.provider, match["id"].lower()
    raise ValueError(
        "Pranohen vetëm video të përfunduara: twitch.tv/videos/ID, kick.com/kanali/videos/UUID ose "
        "youtube.com/watch?v=ID; jo linke live, Shorts apo playlist"
    )


def inbox_file(root: Path, name: str) -> Path:
    """A campaign-supplied file the operator placed in data/inbox. Plain names only: no traversal."""
    if (
        not name
        or Path(name).name != name
        or name.startswith(".")
        or Path(name).suffix.lower() not in VIDEO_SUFFIXES
    ):
        raise ValueError("Zgjidh një skedar video nga dosja data/inbox")
    path = root / "inbox" / name
    if not path.is_file() or path.is_symlink():
        raise ValueError("Skedari nuk gjendet në data/inbox")
    return path


def legacy_file_identity(path: Path) -> str:
    """0.5 fingerprint, used ONLY to locate old jobs for verified lazy migration."""
    digest, size = hashlib.sha256(), path.stat().st_size
    digest.update(str(size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(8 * 1024 * 1024))
        if size > 16 * 1024 * 1024:
            handle.seek(size - 8 * 1024 * 1024)
            digest.update(handle.read())
    return digest.hexdigest()[:32]


def file_identity(path: Path) -> str:
    """Hash every byte with bounded memory; reject a file changed during hashing."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
        after = os.fstat(handle.fileno())

    def signature(s):
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

    if signature(before) != signature(after) or signature(after) != signature(path.stat()):
        raise ValueError("Skedari ndryshoi gjatë leximit; përfundo kopjimin dhe provo përsëri")
    return "sha256:" + digest.hexdigest()


def reconcile_file_identity(store, path: Path, identity: str):
    """Preserve a 0.5 job only when its retained original proves the full-byte match."""
    if store.one("SELECT id FROM jobs WHERE provider='file' AND video_id=?", (identity,)):
        return
    legacy = legacy_file_identity(path)
    job = store.one("SELECT * FROM jobs WHERE provider='file' AND video_id=?", (legacy,))
    if not job:
        return
    metadata = json.loads(job["metadata"])
    saved = metadata.get("source")
    checkpoint = store.config.root / "media" / job["id"] / "download.json"
    if not saved and checkpoint.exists():
        saved = json.loads(checkpoint.read_text(encoding="utf-8")).get("source")
    if not saved or not store.config.path(saved).exists():
        raise ValueError(
            "Ky import përputhet me një identifikues të vjetër 0.5, por origjinali mungon. "
            "Rikthe origjinalin e punës së vjetër para riimportit; nuk mund të provohet nëse është video tjetër."
        )
    previous = file_identity(store.config.path(saved))
    with store.connect(immediate=True) as db:
        # Concurrent imports may already have upgraded this job; never merge unrelated histories.
        other = db.execute("SELECT id FROM jobs WHERE provider='file' AND video_id=?", (previous,)).fetchone()
        if not other:
            db.execute(
                "UPDATE jobs SET source_id=?,video_id=? WHERE id=? AND video_id=?",
                (f"file:{previous}", previous, job["id"], legacy),
            )
    # If previous != identity, create_job creates a separate job for the genuinely different file.


def validate_info(info):
    if not info or info.get("_type") in {"playlist", "url", "url_transparent"}:
        raise ValueError("VOD-i nuk u gjet ose nuk është i arritshëm")
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
        raise ValueError("Streami është live ose ende po përpunohet; përdor VOD të përfunduar")
    if info.get("availability") in {"private", "premium_only", "subscriber_only", "needs_auth"}:
        raise ValueError("VOD-i është privat ose kërkon autorizim")
    if not info.get("duration") or float(info["duration"]) < 20:
        raise ValueError("VOD-i nuk ka kohëzgjatje të vlefshme prej së paku 20 sekondash")

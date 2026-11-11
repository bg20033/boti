"""Bounded VOD trials use real workers and never create a publishing outbox."""

from __future__ import annotations

import json
import shutil
import time

from boti.db import encode
from boti.media import cut_segment, duration
from boti.rules import job_source_allowed
from boti.sources import ADAPTERS, file_identity, inbox_file, validate_info

ROLES = {"transcribe", "analyze", "render", "quality", "repair"}


def migrate(db):
    db.execute("SAVEPOINT trial_migration")
    if "run_mode" not in {r[1] for r in db.execute("PRAGMA table_info(jobs)")}:
        db.execute("ALTER TABLE jobs ADD COLUMN run_mode TEXT NOT NULL DEFAULT 'production'")
        db.execute("ALTER TABLE jobs ADD COLUMN trial_start INTEGER NOT NULL DEFAULT 0")
        db.execute("ALTER TABLE jobs ADD COLUMN trial_seconds INTEGER NOT NULL DEFAULT 0")
        db.execute("ALTER TABLE jobs ADD COLUMN trial_limit INTEGER NOT NULL DEFAULT 0")
        db.execute("ALTER TABLE jobs ADD COLUMN started REAL")
        db.execute("DROP INDEX IF EXISTS job_provider_video")
        db.execute(
            "CREATE UNIQUE INDEX job_provider_video ON jobs(provider,video_id,run_mode,trial_start,trial_seconds,trial_limit)"
        )
    db.execute("UPDATE settings SET value='7' WHERE key='schema_version'")
    db.execute("RELEASE trial_migration")


def readiness(store):
    available = set()
    for worker in store.all(
        "SELECT capabilities FROM workers WHERE paused=0 AND last_seen>?", (time.time() - 30,)
    ):
        available.update(json.loads(worker["capabilities"]))
    missing = sorted(ROLES - available)
    reasons = [f"Mungon agjent për: {kind}" for kind in missing]
    for binary in ("ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            reasons.append(f"Main-it i mungon {binary}")
    settings = store.settings()
    if settings.paused:
        reasons.append("Punët janë pezulluar")
    if shutil.disk_usage(store.config.root).free < settings.min_free_gb * 1024**3:
        reasons.append("Hapësira e diskut është nën prag")
    return {"ready": not reasons, "missing_capabilities": missing, "reasons": reasons}


def prepare_sample(store, job, rules, has_space):
    """Fetch only the requested time range; never fall back to downloading an entire VOD."""
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import download_range_func

    from boti.pipeline import DownloadPaused

    start, seconds = job["trial_start"], job["trial_seconds"]
    directory = store.config.root / "media" / job["id"]
    directory.mkdir(parents=True, exist_ok=True)
    sample = directory / "trial.mp4"
    saved_metadata = directory / "trial-source.json"
    if sample.exists() and saved_metadata.exists():
        metadata = json.loads(saved_metadata.read_text(encoding="utf-8"))
        if abs(duration(sample) - seconds) <= 1:
            return sample, metadata
    if not has_space(seconds * 3_000_000):
        raise DownloadPaused("Prova pret hapësirë për segmentin e zgjedhur")
    if job["provider"] == "file":
        source = inbox_file(store.config.root, job["url"][5:])
        if file_identity(source) != job["video_id"]:
            raise ValueError("Skedari ndryshoi pas nisjes së provës")
        label = json.loads(job["metadata"]).get("source_label") or source.stem
        metadata = {"channel": f"file:{label}", "title": source.stem, "source_duration": duration(source)}
        if not job_source_allowed(rules, {**job, "metadata": metadata}):
            raise ValueError("Burimi i provës nuk lejohet nga fushata")
        if start + seconds > metadata["source_duration"] + 0.1:
            raise ValueError("Intervali i provës është jashtë videos; zgjidh fillim/kohëzgjatje më të vogël")
        cut_segment(source, sample, start, seconds)
        if file_identity(source) != job["video_id"]:
            sample.unlink(missing_ok=True)
            raise ValueError("Skedari ndryshoi gjatë përgatitjes së provës")
    else:
        adapter = ADAPTERS[job["provider"]]
        paused = False

        def progress(_):
            nonlocal paused
            if not has_space():
                paused = True
                raise DownloadPaused("Shkarkimi i provës pret hapësirë në disk")

        options = {
            "format": adapter.format,
            "outtmpl": str(directory / "trial-download.%(ext)s"),
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "retries": 2,
            "fragment_retries": 2,
            "extractor_retries": 1,
            "continuedl": True,
            "skip_unavailable_fragments": False,
            "download_ranges": download_range_func(None, [(start, start + seconds)]),
            "force_keyframes_at_cuts": True,
            "progress_hooks": [progress],
        }
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(job["url"], download=False, ie_key=adapter.extractor)
            validate_info(info)
            metadata = {
                "channel": (f"{job['provider']}:" if job["provider"] != "twitch" else "")
                + str(info.get("uploader_id") or info.get("channel_id") or "unknown"),
                "title": info.get("title") or job["video_id"],
                "source_duration": float(info["duration"]),
                "source_identities": [
                    info[k]
                    for k in (
                        "uploader",
                        "uploader_id",
                        "channel",
                        "channel_id",
                        "uploader_url",
                        "channel_url",
                    )
                    if info.get(k)
                ],
            }
            if not job_source_allowed(rules, {**job, "metadata": metadata}):
                raise ValueError("Burimi i provës nuk lejohet nga fushata")
            if start + seconds > metadata["source_duration"] + 0.1:
                raise ValueError("Intervali i provës është jashtë videos")
            try:
                result = downloader.extract_info(job["url"], download=True, ie_key=adapter.extractor)
            except Exception:
                if paused:
                    raise DownloadPaused("Shkarkimi i provës pret hapësirë në disk") from None
                raise
            from pathlib import Path

            selected = (result.get("requested_downloads") or [{}])[0]
            downloaded = Path(selected.get("filepath") or downloader.prepare_filename(result))
        if abs(duration(downloaded) - seconds) > 1:
            downloaded.unlink(missing_ok=True)
            raise ValueError(
                "Burimi nuk ktheu intervalin e kërkuar; prova ndalet pa përpunuar VOD-in e plotë"
            )
        downloaded.replace(sample)
    metadata.update(source_offset=start, trial_seconds=seconds)
    temp = saved_metadata.with_suffix(".tmp")
    temp.write_text(encode(metadata), encoding="utf-8")
    temp.replace(saved_metadata)
    return sample, metadata


def report(store, job):
    clips = store.all("SELECT * FROM clips WHERE job_id=? ORDER BY score DESC", (job["id"],))
    results = []
    for clip in clips:
        meta = json.loads(clip["metadata"])
        asset = store.one("SELECT path FROM assets WHERE id=?", (clip["asset_id"],))
        reports = store.all(
            "SELECT version,rules_revision,passed,result,created FROM quality_reports WHERE clip_id=? ORDER BY created",
            (clip["id"],),
        )
        for review in reports:
            review["result"] = json.loads(review["result"])
        results.append(
            {
                "id": clip["id"],
                "title": meta.get("title", "Klip"),
                "status": clip["status"],
                "score": clip["score"],
                "version": clip["current_version"],
                "source_start": job["trial_start"] + clip["start"],
                "source_end": job["trial_start"] + clip["end"],
                "selection_reason": meta.get("verification_reason") or meta.get("reason", ""),
                "quality_reason": clip["quality_reason"],
                "quality_reports": reports,
                "video_url": f"/api/clips/{clip['id']}/video"
                if asset and store.config.path(asset["path"]).exists()
                else None,
            }
        )
    now = job["completed"] or (job["updated"] if job["status"] == "failed" else time.time())
    return {
        "job_id": job["id"],
        "run_mode": job["run_mode"],
        "status": job["status"],
        "error": job["error"],
        "range": {"start": job["trial_start"], "seconds": job["trial_seconds"]},
        "clip_limit": job["trial_limit"],
        "elapsed_seconds": max(0, (job["completed"] or now) - job["created"]),
        "processing_seconds": max(0, (job["completed"] or now) - job["started"]) if job["started"] else None,
        "readiness": readiness(store),
        "clips": results,
        "counts": {
            status: sum(c["status"] == status for c in clips)
            for status in ("ready", "skipped", "technical_failed")
        },
        "tasks": store.all(
            "SELECT kind,status,attempts,error,started,finished FROM tasks WHERE job_id=? ORDER BY created",
            (job["id"],),
        ),
        "note": "Provë pa publikim. Kohët përfshijnë pritjen; më pak klipe lejohet kur cilësia nuk mjafton.",
    }

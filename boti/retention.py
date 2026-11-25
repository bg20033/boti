"""Expire finished source caches while retaining every referenced clip version."""

import json
import logging
import shutil
import time

from boti.config import Settings
from boti.db import encode
from boti.storage_budget import ingest_reserve

log = logging.getLogger(__name__)
TERMINAL_CLIPS = ("ready", "archived", "skipped", "technical_failed", "deleted")


def due(job, settings, now, pressure, campaign):
    if settings.delete_source_after_complete:
        return True
    completed = job["completed"]
    return bool(
        completed
        and (
            completed < now - settings.original_retention_days * 86400
            or (job["run_mode"] == "trial" and completed < now - settings.trial_source_retention_hours * 3600)
            or (
                pressure
                and settings.source_cache_pressure_minutes > 0
                and job["run_mode"] == "production"
                and job["review_mode"] == "automatic"
                and job["provider"] in {"kick", "twitch", "youtube"}
                and not campaign
                and completed < now - settings.source_cache_pressure_minutes * 60
            )
        )
    )


def cleanup_sources(store, job_id=None):
    settings, now = store.settings(), time.time()
    budget = shutil.disk_usage(store.config.root).free - settings.min_free_gb * 1024**3
    pressure = settings.source_cache_pressure_minutes > 0 and any(
        ingest_reserve(json.loads(j["metadata"]), store.config.root / "media" / j["id"]) > budget
        for j in store.all("SELECT id,metadata FROM jobs WHERE status='queued'")
    )
    result = {"jobs_cleaned": 0, "files_removed": 0, "bytes_removed": 0, "retry_jobs": 0}
    query = "SELECT id FROM jobs WHERE status='complete'" + (" AND id=?" if job_id else "")
    for row in store.all(query, (job_id,) if job_id else ()):
        try:
            # Re-editing, new task assignments, and deletion serialize on the same lock.
            with store.connect(immediate=True) as db:
                job = db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
                if not job or job["status"] != "complete":
                    continue
                settings = Settings.model_validate_json(
                    db.execute("SELECT value FROM settings WHERE key='config'").fetchone()[0]
                )
                metadata = json.loads(job["metadata"])
                campaign = db.execute("SELECT 1 FROM job_campaigns WHERE job_id=?", (row["id"],)).fetchone()
                if metadata.get("originals_cleaned") or not due(job, settings, now, pressure, campaign):
                    continue
                if db.execute(
                    """SELECT 1 FROM tasks t WHERE t.status IN ('queued','running') AND
                    (t.job_id=? OR EXISTS(SELECT 1 FROM task_assets ta JOIN assets a ON a.id=ta.asset_id
                        WHERE ta.task_id=t.id AND a.job_id=?)) LIMIT 1""",
                    (row["id"], row["id"]),
                ).fetchone():
                    continue
                if db.execute(
                    "SELECT 1 FROM clips WHERE job_id=? AND status NOT IN (?,?,?,?,?) LIMIT 1",
                    (row["id"], *TERMINAL_CLIPS),
                ).fetchone():
                    continue
                # Compare resolved paths as well as IDs: even a second asset alias cannot
                # turn a final output into disposable input.
                protected = {
                    store.config.path(a["path"])
                    for a in db.execute("""SELECT a.path FROM assets a WHERE
                        EXISTS(SELECT 1 FROM clips c WHERE c.asset_id=a.id) OR
                        EXISTS(SELECT 1 FROM clip_versions v WHERE v.asset_id=a.id)""")
                }
                candidates = {
                    store.config.path(a["path"])
                    for a in db.execute(
                        """SELECT a.path FROM assets a WHERE a.job_id=?
                        AND a.task_id IS NULL
                        AND NOT EXISTS(SELECT 1 FROM clips c WHERE c.asset_id=a.id)
                        AND NOT EXISTS(SELECT 1 FROM clip_versions v WHERE v.asset_id=a.id)""",
                        (row["id"],),
                    )
                }
                directory = store.config.path(f"media/{row['id']}")
                for pattern in (
                    "source.*",
                    "trial.mp4",
                    "trial-source.json",
                    "trial-download.*",
                    "audio.wav",
                    "audio.part.wav",
                    "chunk-*.wav",
                    "proxy-*.mp4",
                    "segment-*.mp4",
                ):
                    for path in directory.glob(pattern):
                        resolved = store.config.path(str(path.relative_to(store.config.root)))
                        if not resolved.is_relative_to(directory):
                            raise ValueError("Source cache link leaves its job directory")
                        candidates.add(resolved)
                for path in candidates - protected:
                    if path.is_file():
                        size = path.stat().st_size
                        path.unlink()
                        result["files_removed"] += 1
                        result["bytes_removed"] += size
                metadata["originals_cleaned"] = True
                metadata["originals_cleaned_at"] = now
                db.execute("UPDATE jobs SET metadata=? WHERE id=?", (encode(metadata), row["id"]))
                db.execute(
                    "INSERT INTO events(level,message,job_id,created) VALUES ('info',?,?,?)",
                    ("Videoja burimore dhe cache-i u pastruan; klipet u ruajtën.", row["id"], now),
                )
                result["jobs_cleaned"] += 1
        except (OSError, ValueError):
            # Windows can still hold a just-finished input open. Retry next sweep.
            result["retry_jobs"] += 1
            log.warning("Burimi i përfunduar pret pastrimin: %s", row["id"], exc_info=True)
    return result

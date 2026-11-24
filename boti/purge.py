"""Explicit removal of generated media, with durable deletion on worker archives."""

import re
import shutil
import time
from pathlib import Path

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

SCHEMA = """
CREATE TABLE IF NOT EXISTS deleted_assets (
 asset_id TEXT PRIMARY KEY REFERENCES assets(id), deleted REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS archive_deletions (
 id TEXT PRIMARY KEY, worker_id TEXT NOT NULL REFERENCES workers(id), sha256 TEXT NOT NULL,
 requested REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending', token TEXT, expires REAL NOT NULL DEFAULT 0,
 error TEXT, completed REAL, UNIQUE(worker_id,sha256)
);
"""


def removed(store, asset_id):
    return bool(store.one("SELECT 1 FROM deleted_assets WHERE asset_id=?", (asset_id,)))


def migrate(db):
    if "media_generation" not in {r[1] for r in db.execute("PRAGMA table_info(jobs)")}:
        db.execute("ALTER TABLE jobs ADD COLUMN media_generation INTEGER NOT NULL DEFAULT 0")
    index = db.execute("SELECT sql FROM sqlite_master WHERE name='job_provider_video'").fetchone()
    if not index or "media_generation" not in index[0]:
        db.execute("DROP INDEX IF EXISTS job_provider_video")
        db.execute(
            "CREATE UNIQUE INDEX job_provider_video ON jobs(provider,video_id,run_mode,trial_start,trial_seconds,trial_limit,media_generation)"
        )
    db.execute("INSERT OR IGNORE INTO settings VALUES ('media_generation','0')")


def status(store):
    return store.all("SELECT worker_id,status,count(*) n FROM archive_deletions GROUP BY worker_id,status")


def purge_generated(store):
    """Caller must explicitly authorize deletion. Preserve credentials, inbox, rules and history."""
    from boti.db import uid

    now = time.time()
    settings = store.settings()
    if not settings.paused:
        raise ValueError("Pezullo punën përpara pastrimit të mediave")
    root = store.config.root.resolve()
    failures, deleted_files, freed = [], 0, 0
    with store.connect(immediate=True) as db:
        if db.execute("SELECT 1 FROM publications WHERE status='processing'").fetchone():
            raise ValueError("Prit përfundimin e publikimit aktiv përpara fshirjes")
        clips = db.execute("SELECT * FROM clips WHERE status!='deleted' AND created<=?", (now,)).fetchall()
        jobs = db.execute(
            """SELECT * FROM jobs WHERE created<=? AND (status IN ('complete','failed')
            OR EXISTS(SELECT 1 FROM clips c WHERE c.job_id=jobs.id))""",
            (now,),
        ).fetchall()
        for job in jobs:
            db.execute(
                "INSERT OR IGNORE INTO deleted_assets SELECT id,? FROM assets WHERE job_id=?",
                (now, job["id"]),
            )
            db.execute(
                "UPDATE jobs SET status='failed',stage='purged',error='Media u fshi nga pronari',updated=? "
                "WHERE id=? AND status NOT IN ('complete','failed')",
                (now, job["id"]),
            )
            db.execute(
                "UPDATE tasks SET status='failed',lease_token=NULL,lease_expires=NULL,error='Media u fshi nga pronari',"
                "updated=?,finished=? WHERE job_id=? AND status IN ('queued','running')",
                (now, now, job["id"]),
            )
        db.execute("UPDATE clips SET status='deleted',updated=? WHERE created<=?", (now, now))
        db.execute(
            "UPDATE publications SET status='cancelled',error='Media u fshi nga pronari',updated=? "
            "WHERE status!='published' AND clip_id IN (SELECT id FROM clips WHERE status='deleted')",
            (now,),
        )
        db.execute("DELETE FROM archive_restores WHERE asset_id IN (SELECT asset_id FROM deleted_assets)")
        generation = (
            int(db.execute("SELECT value FROM settings WHERE key='media_generation'").fetchone()[0]) + 1
        )
        db.execute("UPDATE settings SET value=? WHERE key='media_generation'", (str(generation),))
        for pending in db.execute(
            "SELECT id,source_id FROM jobs WHERE status IN ('queued','downloading','processing') AND NOT EXISTS(SELECT 1 FROM clips c WHERE c.job_id=jobs.id)"
        ):
            canonical = re.sub(r":generation:\d+$", "", pending["source_id"]) + f":generation:{generation}"
            db.execute(
                "UPDATE jobs SET source_id=?,media_generation=? WHERE id=?",
                (canonical, generation, pending["id"]),
            )
        for archive in db.execute("""SELECT DISTINCT w.worker_id,w.sha256 FROM worker_archives w
            JOIN deleted_assets d ON d.asset_id=w.asset_id"""):
            db.execute(
                """INSERT INTO archive_deletions(id,worker_id,sha256,requested) VALUES(?,?,?,?)
                ON CONFLICT(worker_id,sha256) DO UPDATE SET status='pending',requested=excluded.requested,
                token=NULL,expires=0,error=NULL,completed=NULL""",
                (uid(), archive["worker_id"], archive["sha256"], now),
            )

    def erase(path, base):
        nonlocal deleted_files, freed
        path, base = Path(path), Path(base).resolve()
        resolved = path.resolve()
        if resolved == base or not resolved.is_relative_to(base):
            raise ValueError("Refused media path outside its managed folder")
        try:
            if path.is_dir():
                # Check every resolved descendant before recursive deletion (including Windows junctions).
                files = list(path.rglob("*"))
                if any(not f.resolve().is_relative_to(base) for f in files):
                    raise ValueError("Refused linked media outside managed storage")
                sizes = [f.stat().st_size for f in files if f.is_file()]
                shutil.rmtree(path)
                deleted_files += len(sizes)
                freed += sum(sizes)
            elif path.is_file():
                size = path.stat().st_size
                path.unlink()
                deleted_files += 1
                freed += size
        except OSError:
            failures.append(str(path))

    for job in jobs:
        if not re.fullmatch(r"[0-9a-f]{32}", job["id"]):
            raise ValueError("Invalid managed job ID")
        erase(root / "media" / job["id"], root / "media")
    for clip in clips:
        erase(root / "exports" / clip["group_id"] / clip["id"], root / "exports")
    # Pending VODs keep their place, but discard old resumable download cache.
    for job in store.all("SELECT id FROM jobs WHERE status='queued' AND created<=?", (now,)):
        directory = root / "media" / job["id"]
        for path in directory.glob("source.*"):
            if path.suffix in {".part", ".ytdl", ".tmp"}:
                erase(path, root / "media")
        store.execute(
            "UPDATE jobs SET metadata=json_remove(metadata,'$.ingest_progress') WHERE id=?", (job["id"],)
        )
    store.event(f"U fshinë {len(clips)} klipe të vjetra; pastrimi i arkivave të workerave u kërkua")
    return {
        "clips_deleted": len(clips),
        "local_files_deleted": deleted_files,
        "bytes_removed": freed,
        "local_failures": failures,
        "worker_deletions": status(store),
    }


class DeleteAck(BaseModel):
    token: str = Field(min_length=1, max_length=100)
    error: str | None = Field(default=None, max_length=300)


class DeleteCursor(BaseModel):
    cache_generation: int = Field(default=0, ge=0)


def routes(app, store, worker):
    @app.post("/api/worker/archive/delete/claim")
    def claim(body: DeleteCursor | None = None, identity=Depends(worker)):
        from boti.db import uid

        with store.connect(immediate=True) as db:
            rows = db.execute(
                "SELECT * FROM archive_deletions WHERE worker_id=? AND status='pending' AND expires<? ORDER BY requested LIMIT 1",
                (identity["id"], time.time()),
            ).fetchall()
            for row in rows:
                live = db.execute(
                    """SELECT 1 FROM worker_archives w WHERE w.worker_id=? AND w.sha256=?
                    AND NOT EXISTS(SELECT 1 FROM deleted_assets d WHERE d.asset_id=w.asset_id)""",
                    (identity["id"], row["sha256"]),
                ).fetchone()
                if live:
                    db.execute(
                        "UPDATE archive_deletions SET status='kept',completed=? WHERE id=?",
                        (time.time(), row["id"]),
                    )
                    continue
                token = uid()
                db.execute(
                    "UPDATE archive_deletions SET token=?,expires=? WHERE id=?",
                    (token, time.time() + 120, row["id"]),
                )
                return {"delete": {"id": row["id"], "sha256": row["sha256"], "token": token}}
        generation = int(store.one("SELECT value FROM settings WHERE key='media_generation'")["value"])
        cache = None
        if generation > (body.cache_generation if body else 0):
            ids = store.all(
                """SELECT t.id FROM tasks t JOIN jobs j ON j.id=t.job_id
                WHERE t.worker_id=? AND j.media_generation<? AND t.status IN ('complete','failed')""",
                (identity["id"], generation),
            )
            cache = {"generation": generation, "task_ids": [r["id"] for r in ids]}
        return {"delete": None, "cache_cleanup": cache}

    @app.post("/api/worker/archive/delete/{deletion_id}/complete")
    def complete(deletion_id: str, body: DeleteAck, identity=Depends(worker)):
        with store.connect(immediate=True) as db:
            row = db.execute("SELECT * FROM archive_deletions WHERE id=?", (deletion_id,)).fetchone()
            if (
                not row
                or row["worker_id"] != identity["id"]
                or row["token"] != body.token
                or row["expires"] < time.time()
                or row["status"] != "pending"
            ):
                raise HTTPException(409, "Pastrimi nuk ka leje aktive")
            db.execute(
                "UPDATE archive_deletions SET status=?,error=?,completed=?,expires=? WHERE id=?",
                (
                    "pending" if body.error else "done",
                    body.error,
                    None if body.error else time.time(),
                    time.time() + 60 if body.error else 0,
                    deletion_id,
                ),
            )
        return {"ok": True}


async def service_one(worker):
    # Called serially before new work, so a new render cannot race deletion of its own hash.
    response = await worker.client.post(
        "/api/worker/archive/delete/claim",
        json={"cache_generation": getattr(worker, "cache_purge_generation", 0)},
    )
    if response.status_code == 404:
        return False
    response.raise_for_status()
    item = response.json().get("delete")
    if not item:
        cache = response.json().get("cache_cleanup")
        if cache and getattr(worker, "cache", None):
            folder = worker.cache.resolve()
            try:
                for task_id in cache["task_ids"]:
                    if not re.fullmatch(r"[0-9a-f]{32}", task_id):
                        raise ValueError("Invalid cache task ID")
                    path = folder / task_id
                    if path.resolve().parent != folder:
                        raise ValueError("Cache leaves managed storage")
                    if path.is_dir():
                        if any(not child.resolve().is_relative_to(folder) for child in path.rglob("*")):
                            raise ValueError("Cache contains external links")
                        shutil.rmtree(path)
                worker.cache_purge_generation = cache["generation"]
            except (OSError, ValueError):
                pass  # Retry locked directories on the next poll; never traverse outside the cache.
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
        raise ValueError("Invalid archive deletion hash")
    folder = worker.archive_dir.resolve()
    path = folder / f"{item['sha256']}.mp4"
    error = None
    try:
        if path.resolve().parent != folder:
            raise ValueError("Archive file leaves managed storage")
        path.unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        error = type(exc).__name__
    response = await worker.client.post(
        f"/api/worker/archive/delete/{item['id']}/complete", json={"token": item["token"], "error": error}
    )
    response.raise_for_status()
    return True

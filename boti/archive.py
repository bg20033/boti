"""Worker-owned immutable videos; outbound-only restoration and a bounded main cache."""

import hashlib
import os
import re
import shutil
import time
from pathlib import Path

from fastapi import Depends, HTTPException, Request

SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_archives (
 asset_id TEXT PRIMARY KEY REFERENCES assets(id), worker_id TEXT NOT NULL REFERENCES workers(id),
 sha256 TEXT NOT NULL, size INTEGER NOT NULL, confirmed REAL NOT NULL, accessed REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS archive_restores (
 asset_id TEXT PRIMARY KEY REFERENCES assets(id), worker_id TEXT NOT NULL REFERENCES workers(id),
 token TEXT, expires REAL NOT NULL DEFAULT 0, requested REAL NOT NULL
);
"""


def retain(output, folder):
    from boti.media import sha256

    digest = sha256(output)
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{digest}.mp4"
    if not target.exists() or sha256(target) != digest:
        temp = target.with_suffix(".part")
        shutil.copyfile(output, temp)
        if sha256(temp) != digest:
            temp.unlink(missing_ok=True)
            raise ValueError("Arkivi nuk kaloi kontrollin SHA256")
        temp.replace(target)
    return {"sha256": digest, "size": target.stat().st_size}


def request_restore(store, asset):
    path = store.config.path(asset["path"])
    with store.connect(immediate=True) as db:
        if path.is_file():
            db.execute("UPDATE worker_archives SET accessed=? WHERE asset_id=?", (time.time(), asset["id"]))
            return True
        location = db.execute("SELECT * FROM worker_archives WHERE asset_id=?", (asset["id"],)).fetchone()
        if location:
            db.execute(
                "INSERT OR IGNORE INTO archive_restores(asset_id,worker_id,requested) VALUES (?,?,?)",
                (asset["id"], location["worker_id"], time.time()),
            )
    return False


def prune_cache(store):
    if not store.settings().worker_archive_enabled:
        return
    cutoff = time.time() - store.settings().worker_cache_minutes * 60
    eligible = """SELECT a.* FROM assets a JOIN worker_archives w ON w.asset_id=a.id
        WHERE w.accessed<? AND NOT EXISTS (SELECT 1 FROM archive_restores r WHERE r.asset_id=a.id)
        AND NOT EXISTS (SELECT 1 FROM task_assets ta JOIN tasks t ON t.id=ta.task_id
          WHERE ta.asset_id=a.id AND t.status IN ('queued','running'))
        AND NOT EXISTS (SELECT 1 FROM clips c WHERE c.asset_id=a.id AND c.status NOT IN ('ready','skipped','technical_failed'))
        AND NOT EXISTS (SELECT 1 FROM clips c JOIN publications p ON p.clip_id=c.id
          WHERE c.asset_id=a.id AND p.status='processing')"""
    for asset in store.all(eligible, (cutoff,)):
        path = store.config.path(asset["path"])
        if not path.is_file():
            continue
        try:
            # Serialize the final eligibility check with touches/restores and new task assignments.
            with store.connect(immediate=True) as db:
                if not db.execute(eligible + " AND a.id=?", (cutoff, asset["id"])).fetchone():
                    continue
                for clip in db.execute("SELECT id,group_id FROM clips WHERE asset_id=?", (asset["id"],)):
                    exported = store.config.root / "exports" / clip["group_id"] / clip["id"] / "video.mp4"
                    if exported.is_file():
                        from boti.media import sha256

                        if os.path.samefile(exported, path) or sha256(exported) == asset["sha256"]:
                            exported.unlink()
                path.unlink()
        except OSError:
            continue  # A video currently being viewed may be locked on Windows.


def routes(app, store, worker):
    @app.post("/api/worker/archive/claim")
    def claim(identity=Depends(worker)):
        from boti.db import uid

        with store.connect(immediate=True) as db:
            row = db.execute(
                """SELECT r.asset_id,a.sha256,a.size FROM archive_restores r JOIN assets a ON a.id=r.asset_id
                WHERE r.worker_id=? AND r.expires<? ORDER BY r.requested LIMIT 1""",
                (identity["id"], time.time()),
            ).fetchone()
            if not row:
                return {"restore": None}
            token = uid()
            db.execute(
                "UPDATE archive_restores SET token=?,expires=? WHERE asset_id=?",
                (token, time.time() + 180, row["asset_id"]),
            )
        return {"restore": {**dict(row), "token": token}}

    @app.put("/api/worker/archive/{asset_id}/restore")
    async def restore(asset_id: str, request: Request, identity=Depends(worker)):
        def authorized():
            row = store.one("SELECT * FROM archive_restores WHERE asset_id=?", (asset_id,))
            if (
                not row
                or row["worker_id"] != identity["id"]
                or row["token"] != request.headers.get("x-restore-token")
                or row["expires"] < time.time()
            ):
                raise HTTPException(409, "Kërkesa e rikthimit skadoi")
            return row

        authorized()
        asset = store.one("SELECT * FROM assets WHERE id=?", (asset_id,))
        if request.headers.get("content-length") != str(asset["size"]):
            raise HTTPException(422, "Madhësia nuk përputhet")
        if shutil.disk_usage(store.config.root).free < asset["size"] + 1024**3:
            raise HTTPException(507, "Main-i pret hapësirë për kopjen e përkohshme")
        target = store.config.path(asset["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        from boti.db import uid

        temp = target.with_name(f"restore-{uid()}.part")
        digest, size = hashlib.sha256(), 0
        try:
            with temp.open("wb") as handle:
                async for block in request.stream():
                    size += len(block)
                    if size > asset["size"]:
                        raise HTTPException(413)
                    digest.update(block)
                    handle.write(block)
            if size != asset["size"] or digest.hexdigest() != asset["sha256"]:
                raise HTTPException(422, "Rikthimi nuk kaloi kontrollin SHA256")
            authorized()
            temp.replace(target)
            with store.connect(immediate=True) as db:
                db.execute("DELETE FROM archive_restores WHERE asset_id=?", (asset_id,))
                db.execute("UPDATE worker_archives SET accessed=? WHERE asset_id=?", (time.time(), asset_id))
            return {"ok": True}
        finally:
            temp.unlink(missing_ok=True)


async def service(worker):
    import asyncio

    folder = worker.archive_dir
    while not worker.stopped.is_set():
        try:
            response = await worker.client.post("/api/worker/archive/claim")
            if response.status_code == 404:
                await asyncio.sleep(30)
                continue
            response.raise_for_status()
            item = response.json().get("restore")
            if item and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                path = folder / f"{item['sha256']}.mp4"
                if path.is_file():

                    def chunks():
                        with path.open("rb") as handle:
                            while block := handle.read(1024 * 1024):
                                yield block

                    # Async generator keeps the worker's main heartbeat independent of the transfer.
                    async def blocks():
                        for block in chunks():
                            yield block

                    result = await worker.client.put(
                        f"/api/worker/archive/{item['asset_id']}/restore",
                        content=blocks(),
                        headers={
                            "X-Restore-Token": item["token"],
                            "Content-Length": str(path.stat().st_size),
                            "Content-Type": "video/mp4",
                        },
                    )
                    result.raise_for_status()
        except Exception:
            pass  # A missing/offline archive never turns into an empty successful video.
        await asyncio.sleep(5)

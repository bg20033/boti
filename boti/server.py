from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from boti import __version__
from boti.config import Config, Settings
from boti.db import Store, token_hash, uid
from boti.media import dependencies, validate_output
from boti.models import (
    AnalysisResult,
    Candidate,
    CompleteRequest,
    FailureRequest,
    GroupCreate,
    GroupUpdate,
    JobCreate,
    SourceProfile,
    Transcript,
    within,
)
from boti.pipeline import Pipeline
from boti.publishing import Publisher
from boti.quality import QualityResult
from boti.sources import VIDEO_SUFFIXES, file_identity, inbox_file, reconcile_file_identity, vod_url

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class Login(BaseModel):
    token: str = Field(max_length=200)


class Heartbeat(BaseModel):
    capabilities: list[str] = Field(default_factory=list, max_length=5)
    hardware: dict = Field(default_factory=dict)
    task_id: str | None = None
    lease_token: str | None = None


class InstagramConnection(BaseModel):
    external_id: str = Field(pattern=r"^\d{3,40}$")
    access_token: str = Field(min_length=20, max_length=2000)


class ManualConnection(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class Review(BaseModel):
    passed: bool
    notes: str = Field(default="", max_length=2000)


class Resolution(BaseModel):
    published: bool
    url: str | None = Field(default=None, max_length=500)
    confirmed_not_published: bool = False


class CoordinatorLock:
    def __init__(self, path):
        self.path = path
        self.handle = None

    def acquire(self):
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        self.handle.write(b"1")
        self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError("Një main tjetër po përdor këtë dosje. Përdor vetëm një proces main.") from exc

    def release(self):
        if self.handle:
            self.handle.close()


def create_app(data_dir=None, *, background=True, secure_cookies=False):
    config = Config(data_dir)
    store = Store(config)
    store.seed()
    login_attempts: dict[str, list[float]] = {}

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        tasks = []
        lock = CoordinatorLock(config.root / "coordinator.lock")
        publisher = None
        if background:
            lock.acquire()
            pipeline = Pipeline(store)
            publisher = Publisher(store)
            from boti.feeds import FeedWatcher

            feeds = FeedWatcher(store)

            async def loop(action, interval):
                while not stop.is_set():
                    try:
                        await asyncio.to_thread(action)
                    except Exception:
                        log.exception("Background operation failed")
                    try:
                        await asyncio.wait_for(stop.wait(), interval)
                    except TimeoutError:
                        pass

            tasks = [
                asyncio.create_task(loop(pipeline.tick, 2)),
                asyncio.create_task(loop(pipeline.ingest_next, 2)),
                asyncio.create_task(loop(publisher.tick, 1)),
                asyncio.create_task(loop(pipeline.cleanup, 3600)),
                asyncio.create_task(loop(feeds.tick, 5)),
            ]
            from boti.archive import prune_cache

            tasks.append(asyncio.create_task(loop(lambda: prune_cache(store), 60)))
        try:
            yield
        finally:
            stop.set()
            # Finish durable stage writes before releasing the single-coordinator lock.
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if publisher:
                publisher.client.close()
            lock.release()

    app = FastAPI(title="Boti Studio", version=__version__, lifespan=lifespan)
    app.state.store = store
    app.state.config = config
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.session_key,
        session_cookie="boti_session",
        max_age=43200,
        same_site="strict",
        https_only=secure_cookies,
    )

    def admin(request: Request):
        authorization = request.headers.get("authorization", "")
        if authorization.startswith("Bearer ") and secrets.compare_digest(
            token_hash(authorization[7:]), store.admin_hash
        ):
            return
        if request.session.get("admin"):
            if request.method not in {"GET", "HEAD"}:
                csrf = request.headers.get("x-csrf-token", "")
                if not csrf or not secrets.compare_digest(csrf, request.session.get("csrf", "")):
                    raise HTTPException(403, "Rifresko panelin dhe provo përsëri")
            return
        raise HTTPException(401, "Hyr në panel me çelësin e main-it")

    from boti.campaigns import routes

    app.include_router(routes(store, admin))
    from boti.feeds import routes as feed_routes

    app.include_router(feed_routes(store, admin))
    from boti.clip_edits import routes as edit_routes

    app.include_router(edit_routes(store, admin))
    from boti.upload_post import routes as provider_routes

    app.include_router(provider_routes(store, admin))

    def worker(request: Request):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Mungon token-i i PC-së")
        value = store.one("SELECT * FROM workers WHERE token_hash=?", (token_hash(authorization[7:]),))
        if not value:
            raise HTTPException(401, "Token i pavlefshëm")
        return value

    def lease(task_id, identity, token):
        try:
            return store.lease(task_id, identity["id"], token or "")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    from boti.archive import routes as archive_routes

    archive_routes(app, store, worker)
    from boti.purge import routes as purge_routes

    purge_routes(app, store, worker)

    @app.get("/healthz")
    def health():
        return {"status": "ok", "version": __version__}

    @app.post("/api/login")
    def login(body: Login, request: Request):
        address = request.client.host if request.client else "unknown"
        now = time.time()
        attempts = [stamp for stamp in login_attempts.get(address, []) if stamp > now - 300]
        login_attempts[address] = attempts
        if len(attempts) >= 10:
            raise HTTPException(429, "Prit pesë minuta para provës tjetër")
        if not secrets.compare_digest(token_hash(body.token.strip()), store.admin_hash):
            attempts.append(now)
            raise HTTPException(401, "Çelësi nuk është i saktë")
        request.session.clear()
        request.session.update(admin=True, csrf=secrets.token_urlsafe(24))
        login_attempts.pop(address, None)
        return {"csrf": request.session["csrf"]}

    @app.get("/api/session", dependencies=[Depends(admin)])
    def session(request: Request):
        return {"csrf": request.session.get("csrf", "")}

    @app.post("/api/logout", dependencies=[Depends(admin)])
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    @app.get("/api/snapshot", dependencies=[Depends(admin)])
    def snapshot():
        result = store.snapshot()
        from boti.feeds import list_feeds

        result["feeds"] = list_feeds(store)
        for publication in result["publications"]:
            publication.pop("state", None)
            metadata = json.loads(publication["metadata"])
            publication["metadata"] = {key: metadata.get(key) for key in ("title", "caption", "hashtags")}
        for job in result["jobs"]:
            job["metadata"] = json.loads(job["metadata"])
            job["group_ids"] = json.loads(job["group_ids"])
        usage = shutil.disk_usage(config.root)
        result["disk"] = {
            "free_gb": round(usage.free / 1024**3, 1),
            "total_gb": round(usage.total / 1024**3, 1),
            "paused": usage.free < store.settings().min_free_gb * 1024**3,
        }
        result["server_time"] = time.time()
        result["version"] = __version__
        capable = {
            cap
            for worker in result["workers"]
            if not worker["paused"] and (worker["last_seen"] or 0) > time.time() - 30
            for cap in worker["assigned_capabilities"]
        }
        missing = sorted({"transcribe", "analyze", "render", "quality", "repair"} - capable)
        result["readiness"] = {"missing_capabilities": missing, "workers_ready": not missing}
        from boti.automation import automation_status

        result["automation"] = automation_status(store, result)
        from boti.operations import operations

        result["operations"] = operations(store, result)
        from boti.purge import status as purge_status

        result["media_cleanup"] = purge_status(store)
        from boti.capacity import estimate

        result["capacity"] = estimate(store)
        return result

    @app.put("/api/settings", dependencies=[Depends(admin)])
    def settings(body: Settings):
        if body.overlap_seconds >= body.chunk_seconds:
            raise HTTPException(422, "Mbivendosja duhet të jetë më e shkurtër se segmenti")
        if not re.fullmatch(r"v\d+\.0", body.instagram_api_version):
            raise HTTPException(422, "Versioni Meta duhet të ketë formën v24.0")
        if body.analysis_worker_id and not store.one(
            "SELECT id FROM workers WHERE id=?", (body.analysis_worker_id,)
        ):
            raise HTTPException(422, "PC-ja e analizës nuk ekziston")
        if body.quality_worker_id and not store.one(
            "SELECT id FROM workers WHERE id=?", (body.quality_worker_id,)
        ):
            raise HTTPException(422, "PC-ja e cilësisë nuk ekziston")
        store.save_settings(body)
        return body

    @app.post("/api/jobs", dependencies=[Depends(admin)])
    def create_job(body: JobCreate):
        groups = body.group_ids or [g["id"] for g in store.all("SELECT id FROM groups WHERE paused=0")]
        if not groups or any(not store.one("SELECT id FROM groups WHERE id=?", (g,)) for g in groups):
            raise HTTPException(422, "Zgjidh së paku një grup ekzistues")
        try:
            if body.file:
                url, provider, metadata = f"file:{body.file}", "file", {"source_label": body.source_label}
                source_path = inbox_file(config.root, body.file)
                source_id = file_identity(source_path)
                if body.trial is None:
                    reconcile_file_identity(store, source_path, source_id)
            else:
                (url, provider, source_id), metadata = vod_url(body.url), None
            job_id, created = store.create_job(
                url,
                source_id,
                list(dict.fromkeys(groups)),
                body.campaign_id,
                provider,
                body.review_mode,
                metadata,
                body.trial.model_dump() if body.trial else None,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": job_id, "created": created}

    @app.get("/api/inbox", dependencies=[Depends(admin)])
    def inbox():
        """Campaign-supplied videos the operator copied into data/inbox on the main PC."""
        folder = config.root / "inbox"
        return {
            "folder": str(folder),
            "files": [
                {"name": path.name, "size": path.stat().st_size}
                for path in sorted(folder.iterdir())
                if path.is_file() and not path.is_symlink() and path.suffix.lower() in VIDEO_SUFFIXES
            ][:200],
        }

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(admin)])
    def job_details(job_id: str):
        job = store.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            raise HTTPException(404, "Puna nuk u gjet")
        return {
            "job": job,
            "campaign": store.one("SELECT campaign_id,revision FROM job_campaigns WHERE job_id=?", (job_id,)),
            "tasks": store.all(
                """SELECT id,kind,status,worker_id,attempts,error,started,finished
               FROM tasks WHERE job_id=? ORDER BY created""",
                (job_id,),
            ),
        }

    @app.get("/api/jobs/{job_id}/trial-report", dependencies=[Depends(admin)])
    def trial_report(job_id: str):
        from boti.trials import report

        job = store.one("SELECT * FROM jobs WHERE id=? AND run_mode='trial'", (job_id,))
        if not job:
            raise HTTPException(404, "Prova nuk u gjet")
        return report(store, job)

    @app.post("/api/jobs/{job_id}/retry", dependencies=[Depends(admin)])
    def retry_job(job_id: str):
        job = store.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job or job["status"] != "failed":
            raise HTTPException(409, "Vetëm punët me gabim mund të riprovohen")
        if store.one(
            "SELECT 1 FROM assets a JOIN deleted_assets d ON d.asset_id=a.id WHERE a.job_id=?", (job_id,)
        ):
            raise HTTPException(409, "Media e vjetër u fshi; shtoje linkun si punë të re")
        metadata = json.loads(job["metadata"])
        processing = bool(metadata.get("source") and config.path(metadata["source"]).exists())
        with store.connect(immediate=True) as db:
            db.execute(
                """UPDATE tasks SET status='queued',attempts=0,error=NULL,lease_token=NULL,
                lease_expires=NULL,not_before=0 WHERE job_id=? AND status='failed'""",
                (job_id,),
            )
            db.execute(
                "UPDATE jobs SET status=?,error=NULL,updated=? WHERE id=?",
                ("processing" if processing else "queued", time.time(), job_id),
            )
        return {"ok": True}

    @app.post("/api/groups", dependencies=[Depends(admin)])
    def add_group(body: GroupCreate):
        if body.preferred_worker and not store.one(
            "SELECT id FROM workers WHERE id=?", (body.preferred_worker,)
        ):
            raise HTTPException(422, "PC-ja nuk ekziston")
        return {"id": store.add_group(body.name, body.preferred_worker)}

    @app.patch("/api/groups/{group_id}", dependencies=[Depends(admin)])
    def pause_group(group_id: str, body: GroupUpdate):
        if not store.execute("UPDATE groups SET paused=? WHERE id=?", (body.paused, group_id)):
            raise HTTPException(404)
        return {"ok": True}

    @app.patch("/api/workers/{worker_id}", dependencies=[Depends(admin)])
    def pause_worker(worker_id: str, body: GroupUpdate):
        if not store.execute("UPDATE workers SET paused=? WHERE id=?", (body.paused, worker_id)):
            raise HTTPException(404)
        return {"ok": True}

    @app.get("/api/sources", dependencies=[Depends(admin)])
    def sources():
        return [
            {"id": row["id"], "profile": json.loads(row["profile"])}
            for row in store.all("SELECT * FROM sources")
        ]

    @app.put("/api/sources/{source_id}", dependencies=[Depends(admin)])
    def source_profile(source_id: str, body: SourceProfile):
        if len(source_id) > 100:
            raise HTTPException(422)
        store.execute(
            "INSERT INTO sources VALUES (?,?) ON CONFLICT(id) DO UPDATE SET profile=excluded.profile",
            (source_id, body.model_dump_json()),
        )
        return {"ok": True}

    @app.post("/api/accounts/{account_id}/instagram", dependencies=[Depends(admin)])
    async def connect_instagram(account_id: str, body: InstagramConnection):
        account = store.one("SELECT * FROM accounts WHERE id=? AND platform='instagram'", (account_id,))
        if not account:
            raise HTTPException(404)
        duplicate = store.one(
            "SELECT id FROM accounts WHERE platform='instagram' AND external_id=? AND id!=?",
            (body.external_id, account_id),
        )
        if duplicate:
            raise HTTPException(409, "Kjo llogari është lidhur tashmë me një grup tjetër")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"https://graph.facebook.com/{store.settings().instagram_api_version}/{body.external_id}",
                    params={"fields": "id,username"},
                    headers={"Authorization": f"Bearer {body.access_token}"},
                )
                if not response.is_success:
                    raise HTTPException(
                        422, "Meta nuk e verifikoi llogarinë. Kontrollo ID-në, token-in dhe lejet."
                    )
                profile = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(502, "Meta nuk arrihet; provo përsëri") from exc
        store.execute(
            "UPDATE accounts SET external_id=?,name=?,credentials=?,status='connected',error=NULL WHERE id=?",
            (
                profile["id"],
                profile.get("username", body.external_id),
                config.encrypt({"access_token": body.access_token}),
                account_id,
            ),
        )
        return {"name": profile.get("username", body.external_id)}

    @app.post("/api/accounts/{account_id}/tiktok", dependencies=[Depends(admin)])
    def connect_tiktok(account_id: str, body: ManualConnection):
        if not store.execute(
            "UPDATE accounts SET name=?,status='manual' WHERE id=? AND platform='tiktok'",
            (body.name, account_id),
        ):
            raise HTTPException(404)
        return {"ok": True}

    @app.post("/api/worker/heartbeat")
    def heartbeat(body: Heartbeat, identity=Depends(worker)):
        if any(
            kind not in {"transcribe", "analyze", "render", "quality", "repair"} for kind in body.capabilities
        ):
            raise HTTPException(422, "Aftësi e panjohur")
        try:
            store.heartbeat(identity["id"], body.capabilities, body.hardware, body.task_id, body.lease_token)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/worker/claim")
    def claim(identity=Depends(worker)):
        if shutil.disk_usage(config.root).free < store.settings().min_free_gb * 1024**3:
            return {"task": None, "reason": "disk_low"}
        return {"task": store.claim(identity["id"])}

    @app.get("/api/worker/tasks/{task_id}/assets/{asset_id}")
    def worker_asset(task_id: str, asset_id: str, request: Request, identity=Depends(worker)):
        lease(task_id, identity, request.headers.get("x-lease-token"))
        asset = store.one(
            "SELECT a.* FROM assets a JOIN task_assets ta ON ta.asset_id=a.id WHERE a.id=? AND ta.task_id=?",
            (asset_id, task_id),
        )
        if not asset or not config.path(asset["path"]).exists():
            raise HTTPException(404)
        return FileResponse(config.path(asset["path"]), media_type="application/octet-stream")

    @app.put("/api/worker/tasks/{task_id}/output")
    async def upload_output(task_id: str, request: Request, identity=Depends(worker)):
        token = request.headers.get("x-lease-token", "")
        task = lease(task_id, identity, token)
        if task["kind"] != "render":
            raise HTTPException(422, "Kjo detyrë nuk pranon video")
        expected_sha = request.headers.get("x-sha256", "")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise HTTPException(422, "Mungon SHA256")
        size_header = request.headers.get("content-length", "")
        if not size_header.isdigit() or not 1 <= int(size_header) <= 1024**3:
            raise HTTPException(413, "Videoja duhet të jetë nën 1 GB dhe të ketë madhësi të deklaruar")
        if shutil.disk_usage(config.root).free < int(size_header) + 1024**3:
            raise HTTPException(507, "Nuk ka hapësirë për rezultatin")
        target = (
            config.root / "media" / task["job_id"] / "results" / f"{task_id}-{token_hash(token)[:12]}.mp4"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.stem}-{uid()}.part")
        digest, size = hashlib.sha256(), 0
        try:
            with temp.open("wb") as handle:
                async for block in request.stream():
                    size += len(block)
                    if size > int(size_header):
                        raise HTTPException(413)
                    digest.update(block)
                    handle.write(block)
            if size != int(size_header) or digest.hexdigest() != expected_sha:
                raise HTTPException(422, "Kontrolli i integritetit dështoi")
            payload = json.loads(task["payload"])
            limits = payload.get("rules") or {}
            try:
                quality = await asyncio.to_thread(
                    validate_output,
                    temp,
                    payload["end"] - payload["start"],
                    limits.get("min_seconds", 20),
                    limits.get("max_seconds", 60),
                )
            except (ValueError, RuntimeError) as exc:
                raise HTTPException(422, str(exc)) from exc
            lease(task_id, identity, token)
            existing = store.one("SELECT * FROM assets WHERE path=?", (str(target.relative_to(config.root)),))
            if existing and existing["sha256"] != expected_sha:
                raise HTTPException(409, "Një rezultat tjetër është dorëzuar për këtë caktim")
            temp.replace(target)
            asset = await asyncio.to_thread(store.add_asset, task["job_id"], target, task_id)
            return {"asset_id": asset["id"], **quality}
        finally:
            temp.unlink(missing_ok=True)

    @app.post("/api/worker/tasks/{task_id}/complete")
    def complete(task_id: str, body: CompleteRequest, identity=Depends(worker)):
        task = lease(task_id, identity, body.lease_token)
        payload = json.loads(task["payload"])
        try:
            if task["kind"] == "transcribe":
                result = Transcript.model_validate(body.result).model_dump()
                for segment in result["segments"]:
                    if segment["end"] > payload["duration"] + 1 or segment["end"] < segment["start"]:
                        raise ValueError("Kohët e transkriptit nuk përputhen me segmentin")
                    for word in segment["words"]:
                        if word["end"] < word["start"] or word["end"] > payload["duration"] + 1:
                            raise ValueError("Kohët e fjalëve janë të pavlefshme")
            elif task["kind"] == "analyze":
                result = AnalysisResult.model_validate(body.result).model_dump()
                if any(c["end"] > payload["duration"] + 0.1 for c in result["candidates"]):
                    raise ValueError("Kandidati është jashtë segmentit")
                if not all(within(c, payload.get("limits")) for c in result["candidates"]):
                    raise ValueError("Kandidati është jashtë kohëzgjatjes së fushatës")
            elif task["kind"] == "quality":
                result = QualityResult.model_validate(body.result).model_dump()
            elif task["kind"] == "repair":
                original = payload["metadata"]
                submitted = body.result["metadata"]
                candidate = Candidate.model_validate(
                    {**original, "start": submitted["start"], "end": submitted["end"]}
                )
                if not within(candidate.model_dump(), payload.get("limits")):
                    raise ValueError("Korrigjimi del jashtë kohëzgjatjes së fushatës")
                if candidate.end > original.get("source_duration", candidate.end) + 0.05:
                    raise ValueError("Korrigjimi del jashtë videos origjinale")
                if payload.get("repair") != "boundaries" and (candidate.start, candidate.end) != (
                    original["start"],
                    original["end"],
                ):
                    raise ValueError("Vetëm riparimi i prerjes mund të ndryshojë kohët")
                if (
                    abs(candidate.start - original["start"]) > 2.2
                    or abs(candidate.end - original["end"]) > 6.2
                ):
                    raise ValueError("Korrigjimi tejkalon kufijtë e lejuar")
                segments = Transcript.model_validate({"segments": submitted["segments"]}).model_dump()[
                    "segments"
                ]
                length = candidate.end - candidate.start
                if any(
                    not 0 <= s["start"] <= s["end"] <= length + 0.05
                    or any(not 0 <= w["start"] <= w["end"] <= length + 0.05 for w in s["words"])
                    for s in segments
                ):
                    raise ValueError("Titrat janë jashtë klipit të korrigjuar")
                result = {
                    "metadata": {
                        **original,
                        "start": candidate.start,
                        "end": candidate.end,
                        "segments": segments,
                        "force_fit": True,
                    }
                }
            else:
                asset = store.one(
                    "SELECT * FROM assets WHERE id=? AND task_id=?", (body.result.get("asset_id"), task_id)
                )
                if not asset:
                    raise ValueError("Dorëzo së pari videon e verifikuar")
                from boti.styles import EditStyle, render_revision

                expected_style = EditStyle.model_validate(payload.get("editing") or {}).render_options()
                if render_revision(payload) and body.result.get("editing") != expected_style:
                    raise ValueError("Workeri nuk konfirmoi stilin e kërkuar; përditëso dhe rinise worker-in")
                # Overlay evidence is what the worker's own renderer reports; the compliance check
                # compares it with the campaign rules, so only the expected shape is kept.
                reported = body.result.get("overlays")
                reported = reported if isinstance(reported, dict) else {}
                result = {
                    "asset_id": asset["id"],
                    "layout": str(body.result.get("layout", ""))[:30],
                    "editing": expected_style,
                    "overlays": {
                        "captions": bool(reported.get("captions", True)),
                        "watermark": str(reported.get("watermark", ""))[:40],
                        "logo": str(reported.get("logo", ""))[:64],
                        "hook": bool(reported.get("hook", False)),
                    },
                }
            store.finish(task_id, identity["id"], body.lease_token, result=result)
            archive = body.result.get("archive") if task["kind"] == "render" else None
            if (
                isinstance(archive, dict)
                and archive.get("sha256") == asset["sha256"]
                and archive.get("size") == asset["size"]
            ):
                store.execute(
                    "INSERT OR REPLACE INTO worker_archives VALUES (?,?,?,?,?,?)",
                    (asset["id"], identity["id"], asset["sha256"], asset["size"], time.time(), time.time()),
                )
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/worker/tasks/{task_id}/fail")
    def fail(task_id: str, body: FailureRequest, identity=Depends(worker)):
        try:
            store.finish(
                task_id, identity["id"], body.lease_token, error=body.error, retryable=body.retryable
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @app.get("/api/clips", dependencies=[Depends(admin)])
    def clips(
        limit: int = 50,
        offset: int = 0,
        state: Literal["all", "ready", "review", "skipped", "processing"] = "all",
        q: str = Query(default="", max_length=200),
    ):
        if not 1 <= limit <= 200 or offset < 0:
            raise HTTPException(422)
        states = {
            "ready": ("ready", "archived"),
            "review": ("checking", "correcting", "approved"),
            "skipped": ("skipped", "technical_failed"),
            "processing": ("preparing", "rendering"),
        }
        conditions, parameters = ["c.status!='deleted'"], []
        if state != "all":
            conditions.append("c.status IN (" + ",".join("?" for _ in states[state]) + ")")
            parameters.extend(states[state])
        if q.strip():
            search = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conditions.append(
                "(json_extract(c.metadata,'$.title') LIKE ? ESCAPE '\\' OR g.name LIKE ? ESCAPE '\\')"
            )
            parameters.extend((search, search))
        where = " AND ".join(conditions) or "1=1"
        total = store.one(
            f"SELECT count(*) n FROM clips c JOIN groups g ON g.id=c.group_id WHERE {where}", parameters
        )["n"]
        result = store.all(
            f"""SELECT c.*,j.run_mode,j.provider,g.name group_name,r.passed review_passed,r.notes review_notes
                FROM clips c JOIN jobs j ON j.id=c.job_id JOIN groups g ON g.id=c.group_id
                LEFT JOIN reviews r ON r.clip_id=c.id WHERE {where} ORDER BY c.created DESC,c.id DESC LIMIT ? OFFSET ?""",
            (*parameters, limit, offset),
        )
        from boti.campaigns import clip_compliance
        from boti.pipeline import caption
        from boti.rules import clip_rules

        for item in result:
            item["metadata"] = json.loads(item["metadata"])
            asset = store.one("SELECT path FROM assets WHERE id=?", (item["asset_id"],))
            item["video_url"] = (
                f"/api/clips/{item['id']}/video" if asset and config.path(asset["path"]).exists() else None
            )
            linked = store.one("SELECT rules_snapshot FROM job_campaigns WHERE job_id=?", (item["job_id"],))
            # What will actually be posted, and whether this version meets the campaign's clip rules.
            item["compliance"] = clip_compliance(store, item)
            item["post_text"] = caption(
                {"title": "Klip", **item["metadata"]},
                clip_rules(linked["rules_snapshot"]) if linked else None,
            )
            item["quality_reports"] = store.all(
                "SELECT version,asset_id,sha256,metadata_hash,rules_revision,passed,result,created FROM quality_reports WHERE clip_id=? ORDER BY created",
                (item["id"],),
            )
            for report in item["quality_reports"]:
                report["result"] = json.loads(report["result"])
            item["versions"] = store.all(
                "SELECT version,asset_id,metadata,created FROM clip_versions WHERE clip_id=? ORDER BY version",
                (item["id"],),
            )
            for version in item["versions"]:
                version["metadata"] = json.loads(version["metadata"])
            item["publications"] = store.all(
                """SELECT p.id,p.platform,p.status,p.url,p.error,a.status account_status FROM publications p
                JOIN accounts a ON a.id=p.account_id WHERE p.clip_id=?""",
                (item["id"],),
            )
            job = store.one("SELECT metadata,review_mode FROM jobs WHERE id=?", (item["job_id"],))
            source = json.loads(job["metadata"]).get("source")
            item["can_edit_title"] = bool(
                job["review_mode"] == "automatic"
                and not item["publications"]
                and source
                and config.path(source).is_file()
            )
            location = (
                store.one("SELECT worker_id FROM worker_archives WHERE asset_id=?", (item["asset_id"],))
                if item["asset_id"]
                else None
            )
            item["storage_worker"] = location["worker_id"] if location else None
            for publication in item["publications"]:
                if publication["status"] == "queued" and publication["account_status"] != "connected":
                    publication["display_status"] = "awaiting_connection"
        return {"clips": result, "total": total, "limit": limit, "offset": offset}

    @app.get("/api/clips/{clip_id}/video", dependencies=[Depends(admin)])
    async def clip_video(clip_id: str):
        clip = store.one("SELECT asset_id,status FROM clips WHERE id=?", (clip_id,))
        if clip and clip["status"] == "deleted":
            raise HTTPException(410, "Videoja u fshi nga pronari")
        asset = store.one("SELECT * FROM assets WHERE id=?", (clip["asset_id"],)) if clip else None
        if not asset:
            raise HTTPException(404, "Videoja nuk është gati ose është arkivuar")
        from boti.archive import request_restore

        for _ in range(60):
            if request_restore(store, asset):
                return FileResponse(config.path(asset["path"]), media_type="video/mp4")
            if not store.one("SELECT 1 FROM worker_archives WHERE asset_id=?", (asset["id"],)):
                raise HTTPException(404, "Videoja nuk është gati ose është arkivuar")
            await asyncio.sleep(0.5)
        raise HTTPException(
            503,
            "Videoja pret rikthimin nga workeri; kontrollo nëse PC-ja është online",
            headers={"Retry-After": "10"},
        )

    @app.get("/api/clips/{clip_id}/tiktok.zip", dependencies=[Depends(admin)])
    async def tiktok_zip(clip_id: str, background_tasks: BackgroundTasks):
        clip = store.one("SELECT * FROM clips WHERE id=? AND status='ready'", (clip_id,))
        if not clip:
            raise HTTPException(404)
        from boti.campaigns import publish_allowed

        if not publish_allowed(store, clip):
            raise HTTPException(409, "Klipi pret kontrollin aktual ose konfirmimet e fushatës")
        folder = config.root / "exports" / clip["group_id"] / clip_id
        from boti.archive import request_restore

        asset = store.one("SELECT * FROM assets WHERE id=?", (clip["asset_id"],))
        if not request_restore(store, asset):
            raise HTTPException(
                503, "Paketa pret videon nga workeri; provo përsëri pas pak", headers={"Retry-After": "10"}
            )
        Pipeline(store).export_tiktok(clip, clip["asset_id"])
        if not (folder / "video.mp4").exists():
            raise HTTPException(404)
        target = config.root / "exports" / f"download-{uid()}.zip"

        def package():
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
                for name in ("video.mp4", "caption.txt", "metadata.json"):
                    archive.write(folder / name, name)

        await asyncio.to_thread(package)
        background_tasks.add_task(target.unlink, missing_ok=True)
        return FileResponse(target, filename=f"boti-{clip_id[:10]}.zip", background=background_tasks)

    @app.post("/api/clips/{clip_id}/review", dependencies=[Depends(admin)])
    def review(clip_id: str, body: Review):
        if not store.one("SELECT id FROM clips WHERE id=?", (clip_id,)):
            raise HTTPException(404)
        store.execute(
            "INSERT INTO reviews VALUES (?,?,?,?) ON CONFLICT(clip_id) DO UPDATE SET passed=excluded.passed,"
            "notes=excluded.notes,updated=excluded.updated",
            (clip_id, body.passed, body.notes, time.time()),
        )
        return {"ok": True}

    @app.post("/api/publications/{publication_id}/resolve", dependencies=[Depends(admin)])
    def resolve(publication_id: str, body: Resolution):
        publication = store.one("SELECT * FROM publications WHERE id=?", (publication_id,))
        if not publication:
            raise HTTPException(404)
        if body.published:
            if body.url:
                parsed = httpx.URL(body.url)
                allowed = {
                    "youtube": {"www.youtube.com", "youtube.com", "youtu.be"},
                    "instagram": {"www.instagram.com", "instagram.com"},
                    "tiktok": {"www.tiktok.com", "tiktok.com"},
                    "x": {"x.com", "www.x.com", "twitter.com", "www.twitter.com"},
                }[publication["platform"]]
                if parsed.scheme != "https" or parsed.host not in allowed:
                    raise HTTPException(422, "Përdor linkun HTTPS të publikimit në platformën përkatëse")
            store.execute(
                "UPDATE publications SET status='published',url=?,error=NULL,updated=? WHERE id=?",
                (body.url, time.time(), publication_id),
            )
        else:
            if not body.confirmed_not_published:
                raise HTTPException(422, "Verifiko që videoja nuk është publikuar para riprovimit")
            account = store.one("SELECT settings FROM accounts WHERE id=?", (publication["account_id"],))
            if (
                publication["platform"] == "tiktok"
                and json.loads(account["settings"]).get("provider") != "upload_post"
            ):
                status = "manual_ready"
            else:
                status = "queued"
            store.execute(
                "UPDATE publications SET status=?,state='{}',attempts=0,external_id=NULL,url=NULL,error=NULL,not_before=0,updated=? WHERE id=?",
                (status, time.time(), publication_id),
            )
        return {"ok": True}

    @app.get("/api/doctor", dependencies=[Depends(admin)])
    def doctor():
        return {
            "tools": dependencies(),
            "free_gb": round(shutil.disk_usage(config.root).free / 1024**3, 1),
            "publishing_enabled": store.settings().publishing_enabled,
        }

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        page = re.sub(
            r'((?:src|href)="/static/[^"]+)"',
            lambda match: match[1].split("?")[0] + f'?v={__version__}"',
            page,
        )
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    return app

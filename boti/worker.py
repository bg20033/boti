from __future__ import annotations

import asyncio
import importlib.util
import logging
import platform
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import psutil
from pydantic import BaseModel, Field, field_validator

from boti import __version__
from boti.ai import LocalAI, transcribe
from boti.media import available_encoder, dependencies, render, sha256
from boti.styles import RENDER_REVISION

log = logging.getLogger(__name__)


class WorkerConfig(BaseModel):
    id: str
    token: str = Field(min_length=20)
    coordinator_url: str
    cache_dir: str = "worker-data"
    archive_dir: str | None = None
    capabilities: list[str] = Field(
        default_factory=lambda: ["transcribe", "analyze", "render", "quality", "repair"]
    )
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3.5:4b"
    whisper_device: str = Field(default="cpu", pattern="^(cpu|cuda)$")
    whisper_model: str = "medium"
    encoder: str = Field(default="auto", pattern="^(auto|cpu)$")
    verify_tls: bool | str = True

    @field_validator("coordinator_url", "ollama_url")
    @classmethod
    def valid_url(cls, value):
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Expected an HTTP(S) URL without embedded credentials")
        return value.rstrip("/")


def inspect_hardware(config: WorkerConfig, probe_ai=False) -> dict:
    tools = dependencies()
    hardware = {
        "boti_version": __version__,
        "render_revision": RENDER_REVISION,
        "os": platform.platform(),
        "cpu_count": psutil.cpu_count(),
        "ram_gb": round(psutil.virtual_memory().total / 1024**3, 1),
        "ffmpeg": tools,
        "whisper": importlib.util.find_spec("faster_whisper") is not None,
        "ollama": False,
        "model": config.model,
    }
    try:
        response = httpx.get(f"{config.ollama_url}/api/tags", timeout=5)
        response.raise_for_status()
        hardware["ollama"] = any(m.get("name") == config.model for m in response.json().get("models", []))
    except (httpx.HTTPError, ValueError):
        pass
    hardware["encoder"] = available_encoder(config.encoder) if tools.get("ffmpeg") else "unavailable"
    capabilities = []
    if hardware["whisper"]:
        capabilities.append("transcribe")
    if hardware["ollama"] and all(tools.get(k) for k in ("ffmpeg", "ffprobe")):
        capabilities.append("analyze")
        if hardware["whisper"]:
            capabilities.append("quality")
    if all(tools.get(k) for k in ("ffmpeg", "ffprobe", "subtitles")):
        capabilities.append("render")
    if hardware["whisper"] and tools.get("ffmpeg"):
        capabilities.append("repair")
    # Existing v0.3 configs gain the new roles only alongside their original analysis/transcription roles.
    allowed = set(config.capabilities)
    if "analyze" in allowed:
        allowed.add("quality")
    if "transcribe" in allowed:
        allowed.add("repair")
    hardware["capabilities"] = [c for c in capabilities if c in allowed]
    if probe_ai and any(c in hardware["capabilities"] for c in ("analyze", "quality")):
        try:
            with httpx.Client(timeout=300) as client:
                result = LocalAI(config.ollama_url, config.model, client).chat(
                    "Return the requested JSON only.",
                    'Return {"ok": true}.',
                    {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
                )
                hardware["ai_probe"] = result.get("ok") is True
                client.post(
                    f"{config.ollama_url}/api/generate", json={"model": config.model, "keep_alive": 0}
                )
            if not hardware["ai_probe"]:
                hardware["capabilities"] = [
                    c for c in hardware["capabilities"] if c not in {"analyze", "quality"}
                ]
        except Exception as exc:
            hardware["ai_probe"] = False
            hardware["ai_error"] = f"Modeli nuk e përfundoi provën: {type(exc).__name__}"
            hardware["capabilities"] = [
                c for c in hardware["capabilities"] if c not in {"analyze", "quality"}
            ]
    return hardware


class Worker:
    def __init__(self, config: WorkerConfig):
        self.config = config
        self.cache = Path(config.cache_dir).resolve() / config.id
        self.cache.mkdir(parents=True, exist_ok=True)
        self.archive_dir = (
            Path(config.archive_dir).resolve()
            if config.archive_dir
            else self.cache.parent / "archive" / config.id
        )
        self.hardware = inspect_hardware(config)
        self.active: dict | None = None
        self.lost_lease = False
        self.stopped = asyncio.Event()
        self.client = httpx.AsyncClient(
            base_url=config.coordinator_url,
            headers={"Authorization": f"Bearer {config.token}"},
            verify=config.verify_tls,
            timeout=httpx.Timeout(360, connect=15),
        )

    async def heartbeat(self):
        while not self.stopped.is_set():
            body = {
                "capabilities": self.hardware["capabilities"],
                "hardware": {
                    **self.hardware,
                    "cpu_percent": psutil.cpu_percent(),
                    "ram_percent": psutil.virtual_memory().percent,
                    "free_gb": round(shutil.disk_usage(self.cache).free / 1024**3, 1),
                    "archive_folder": str(self.archive_dir),
                    "archive_protocol": 1,
                },
            }
            if self.active:
                body.update(task_id=self.active["id"], lease_token=self.active["lease_token"])
            try:
                response = await self.client.post("/api/worker/heartbeat", json=body)
                if response.status_code == 409:
                    self.lost_lease = True
                response.raise_for_status()
            except httpx.HTTPError:
                log.warning("Main-i nuk arrihet; agjenti pret lidhjen")
            try:
                await asyncio.wait_for(self.stopped.wait(), timeout=10)
            except TimeoutError:
                pass

    async def download_assets(self, task, directory):
        paths = {}
        for asset in task["assets"]:
            destination = directory / (asset["id"] + Path(asset["name"]).suffix)
            if not destination.exists() or sha256(destination) != asset["sha256"]:
                temp = destination.with_suffix(".part")
                size = 0
                async with self.client.stream(
                    "GET",
                    f"/api/worker/tasks/{task['id']}/assets/{asset['id']}",
                    headers={"X-Lease-Token": task["lease_token"]},
                ) as response:
                    response.raise_for_status()
                    with temp.open("wb") as handle:
                        async for block in response.aiter_bytes():
                            size += len(block)
                            if size > asset["size"]:
                                raise ValueError("Transferimi tejkaloi madhësinë e deklaruar")
                            handle.write(block)
                if size != asset["size"] or sha256(temp) != asset["sha256"]:
                    temp.unlink(missing_ok=True)
                    raise ValueError("Kontrolli i integritetit të segmentit dështoi")
                temp.replace(destination)
            paths[asset["id"]] = destination
        return paths

    def process(self, task, paths, directory):
        payload = task["payload"]
        if task["kind"] == "transcribe":
            return transcribe(
                paths[payload["audio_id"]],
                self.config.whisper_model,
                self.config.whisper_device,
                language=payload.get("language"),
                vocabulary=payload.get("vocabulary", ""),
            )
        if task["kind"] == "analyze":
            with httpx.Client(timeout=1200) as client:
                return LocalAI(self.config.ollama_url, self.config.model, client).analyze(
                    paths[payload["audio_id"]],
                    paths[payload["proxy_id"]],
                    payload["transcript"],
                    payload["duration"],
                    directory,
                    payload["threshold"],
                    payload.get("limits"),
                )
        if task["kind"] == "quality":
            from boti.quality import check_rendered

            with httpx.Client(timeout=1200) as client:
                return check_rendered(
                    LocalAI(self.config.ollama_url, self.config.model, client),
                    paths[payload["asset_id"]],
                    payload,
                    directory,
                    self.config,
                )
        if task["kind"] == "repair":
            from boti.quality import repair_metadata

            return repair_metadata(payload, paths[payload["segment_id"]], self.config)
        if task["kind"] == "render":
            destination = directory / "output.mp4"
            result = render(
                paths[payload["segment_id"]],
                destination,
                payload,
                self.hardware["encoder"],
                paths.get(payload.get("logo_id")),
            )
            return {**result, "output": str(destination)}
        raise ValueError("Unknown task kind")

    async def execute_task(self, task):
        self.active, self.lost_lease = task, False
        directory = self.cache / task["id"] / task["lease_token"][:12]
        directory.mkdir(parents=True, exist_ok=True)
        try:
            paths = await self.download_assets(task, directory)
            result = await asyncio.to_thread(self.process, task, paths, directory)
            if self.lost_lease:
                log.warning("Caktimi skadoi; rezultati nuk dorëzohet")
                return
            if task["kind"] == "render":
                output = Path(result.pop("output"))
                from boti.archive import retain

                result["archive"] = await asyncio.to_thread(retain, output, self.archive_dir)

                async def blocks():
                    with output.open("rb") as handle:
                        while block := handle.read(1024 * 1024):
                            yield block

                response = await self.client.put(
                    f"/api/worker/tasks/{task['id']}/output",
                    content=blocks(),
                    headers={
                        "X-Lease-Token": task["lease_token"],
                        "Content-Type": "video/mp4",
                        "Content-Length": str(output.stat().st_size),
                        "X-SHA256": sha256(output),
                    },
                )
                response.raise_for_status()
                result.update(response.json())
            response = await self.client.post(
                f"/api/worker/tasks/{task['id']}/complete",
                json={"lease_token": task["lease_token"], "result": result},
            )
            response.raise_for_status()
            log.info("U përfundua %s: %s", task["kind"], task["id"])
        except Exception as exc:
            log.error("Detyra %s dështoi (%s)", task["id"], type(exc).__name__)
            # Request URLs include only internal identifiers, never credentials or access tokens.
            error = (
                str(exc)[:1200]
                if not isinstance(exc, httpx.HTTPError)
                else f"Lidhja/API: {type(exc).__name__}"
            )
            try:
                await self.client.post(
                    f"/api/worker/tasks/{task['id']}/fail",
                    json={"lease_token": task["lease_token"], "error": error, "retryable": True},
                )
            except httpx.HTTPError:
                pass
        finally:
            self.active = None
            shutil.rmtree(directory, ignore_errors=True)

    async def run(self):
        heartbeat = asyncio.create_task(self.heartbeat())
        from boti.archive import service

        archive_service = asyncio.create_task(service(self))
        checked = time.monotonic()
        log.info(
            "%s u nis. Aftësitë: %s",
            self.config.id,
            ", ".join(self.hardware["capabilities"]) or "asnjë; shih doctor",
        )
        try:
            while not self.stopped.is_set():
                if time.monotonic() - checked > 300:
                    self.hardware = await asyncio.to_thread(inspect_hardware, self.config)
                    checked = time.monotonic()
                try:
                    response = await self.client.post("/api/worker/claim")
                    response.raise_for_status()
                    task = response.json().get("task")
                    if task:
                        await self.execute_task(task)
                        continue
                except httpx.HTTPError:
                    log.warning("Radha nuk arrihet; riprovim pas 5 sekondash")
                await asyncio.sleep(5)
        finally:
            self.stopped.set()
            await heartbeat
            await archive_service
            await self.client.aclose()


def run_worker(path: Path):
    config = WorkerConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
    asyncio.run(Worker(config).run())

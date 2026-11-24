from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from boti.ai import select_candidates
from boti.db import Store, encode, uid
from boti.editing import boundary_evidence, refine_boundaries
from boti.media import audio_slice, cut_segment, duration, extract_audio, make_proxy
from boti.models import within
from boti.rules import (
    analysis_limits,
    banned_hits,
    chunking,
    clip_rules,
    final_caption,
    job_source_allowed,
    render_rules,
    source_allowed,
    spoken_text,
)
from boti.sources import ADAPTERS, file_identity, inbox_file, validate_info

log = logging.getLogger(__name__)


class DownloadPaused(Exception):
    pass


class Pipeline:
    def __init__(self, store: Store):
        self.store = store
        self._proxies_left = 2
        self._renders_left = 4
        # A previous coordinator process may have stopped between durable stages.
        store.execute("UPDATE jobs SET status='queued' WHERE status='downloading'")

    def tick(self):
        self.store.reap()
        if self.store.settings().paused:
            return
        self.advance()

    def ingest_next(self):
        settings = self.store.settings()
        if settings.paused:
            return
        active = self.store.one("SELECT count(*) n FROM jobs WHERE status IN ('processing','downloading')")[
            "n"
        ]
        if active >= settings.max_active_jobs:
            return
        from boti.trials import readiness

        queued = None
        for candidate in self.store.all("SELECT * FROM jobs WHERE status='queued' ORDER BY created"):
            if candidate["run_mode"] == "trial" and not readiness(self.store)["ready"]:
                continue
            queued = candidate
            break
        if queued:
            if not self.has_space(json.loads(queued["metadata"]).get("reserve_bytes", 0)):
                return
            try:
                self.ingest(queued)
            except DownloadPaused as exc:
                self.store.execute(
                    "UPDATE jobs SET status='queued',error=?,updated=? WHERE id=?",
                    (str(exc), time.time(), queued["id"]),
                )
            except Exception as exc:
                log.exception("Ingestion failed for %s", queued["id"])
                self.fail_job(queued["id"], str(exc))

    def campaign_rules(self, job_id) -> dict:
        """The rules snapshot this job was confirmed against; empty for jobs without a campaign."""
        linked = self.store.one("SELECT rules_snapshot FROM job_campaigns WHERE job_id=?", (job_id,))
        return json.loads(linked["rules_snapshot"]) if linked else {}

    def fail_job(self, job_id, message):
        self.store.execute(
            "UPDATE jobs SET status='failed',error=?,updated=? WHERE id=?",
            (message[:2000], time.time(), job_id),
        )
        self.store.event(message, "error", job_id)

    def ingest(self, job: dict, source: Path | None = None, metadata: dict | None = None):
        from yt_dlp import YoutubeDL

        settings = self.store.settings()
        campaign = self.campaign_rules(job["id"])
        rules = clip_rules(campaign)
        directory = self.store.config.root / "media" / job["id"]
        directory.mkdir(parents=True, exist_ok=True)
        self.store.execute(
            "UPDATE jobs SET status='downloading',stage='download',started=coalesce(started,?),updated=? WHERE id=?",
            (time.time(), time.time(), job["id"]),
        )
        checkpoint = directory / "download.json"
        if source is None and checkpoint.exists():
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            existing_source = self.store.config.path(saved["source"])
            if existing_source.exists():
                source, metadata = existing_source, saved["metadata"]
        if source is None and job.get("run_mode") == "trial":
            from boti.trials import prepare_sample

            source, metadata = prepare_sample(self.store, job, rules, self.has_space)
        if source is None and job.get("provider") == "file":
            # Campaign-supplied file from data/inbox. Its label keys the streamer profile, like a channel.
            label = json.loads(job["metadata"]).get("source_label") or Path(job["url"][5:]).stem
            if not source_allowed(rules, label, job["url"][5:]):
                raise ValueError("Ky skedar nuk është në burimet e lejuara të fushatës")
            source = inbox_file(self.store.config.root, job["url"][5:])
            metadata = {"channel": f"file:{label}", "title": Path(job["url"][5:]).stem}
        if source is None:
            pause_reason = None
            checked_at = 0
            reserve_bytes = 0

            def progress(_):
                nonlocal pause_reason, checked_at
                if time.monotonic() - checked_at < 1:
                    return
                checked_at = time.monotonic()
                if not self.has_space(reserve_bytes):
                    pause_reason = "Shkarkimi pret hapësirë në disk; pjesa e shkarkuar ruhet"
                    raise DownloadPaused(pause_reason)

            adapter = ADAPTERS[job.get("provider", "twitch")]
            options = {
                "format": adapter.format,
                "merge_output_format": "mp4",
                "outtmpl": str(directory / "source.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "retries": 3,
                "fragment_retries": 3,
                "extractor_retries": 2,
                "skip_unavailable_fragments": False,
                "continuedl": True,
                "socket_timeout": 30,
                "restrictfilenames": True,
                "progress_hooks": [progress],
                "match_filter": lambda info, **_: (
                    "Vetëm VOD-e të përfunduara" if info.get("is_live") else None
                ),
            }
            with YoutubeDL(options) as downloader:
                # Probe the actual VOD before activating its download. No generic extractor fallback.
                try:
                    info = downloader.extract_info(job["url"], download=False, ie_key=adapter.extractor)
                    validate_info(info)
                except Exception as exc:
                    raise ValueError(
                        f"{adapter.provider}: VOD-i është privat, i paarritshëm ose i papërfunduar: {str(exc)[:700]}"
                    ) from exc
                if not source_allowed(
                    rules,
                    job["url"],
                    *(
                        info.get(key)
                        for key in (
                            "uploader",
                            "uploader_id",
                            "channel",
                            "channel_id",
                            "uploader_url",
                            "channel_url",
                        )
                    ),
                ):
                    raise ValueError(
                        "Ky kanal nuk është në burimet e lejuara të fushatës. Shto burimin te rregullat "
                        "vetëm nëse fushata e autorizon."
                    )
                reserve_bytes = int(float(info.get("duration") or 0) * 90000)
                self.store.execute(
                    "UPDATE jobs SET metadata=? WHERE id=?",
                    (encode({"reserve_bytes": reserve_bytes}), job["id"]),
                )
                progress({})
                try:
                    info = downloader.extract_info(job["url"], download=True, ie_key=adapter.extractor)
                except Exception:
                    if pause_reason:
                        raise DownloadPaused(pause_reason) from None
                    raise
                # Merged audio+video downloads end up in a different file than the template predicts.
                merged = (info.get("requested_downloads") or [{}])[0].get("filepath")
                source = Path(merged or downloader.prepare_filename(info))
            metadata = {
                "channel": (
                    f"{job['provider']}:{info.get('uploader_id') or info.get('channel_id') or 'unknown'}"
                    if job.get("provider") in {"kick", "youtube"}
                    else str(info.get("uploader_id") or info.get("channel_id") or "unknown")
                ),
                "title": info.get("title") or job["source_id"],
                "source_identities": [
                    info[key]
                    for key in (
                        "uploader",
                        "uploader_id",
                        "channel",
                        "channel_id",
                        "uploader_url",
                        "channel_url",
                    )
                    if info.get(key)
                ],
            }
        elif not source.is_relative_to(directory):
            destination = directory / f"source{source.suffix.lower()}"
            temp = destination.with_name(destination.name + ".part")
            temp.unlink(missing_ok=True)
            if not self.has_space(source.stat().st_size):
                raise DownloadPaused("Importi pret hapësirë për kopjen e pandryshueshme të burimit")
            shutil.copyfile(source, temp)  # Inbox edits must not change the job's retained original.
            temp.replace(destination)
            source = destination
        if (
            job.get("run_mode") != "trial"
            and job.get("provider") == "file"
            and job.get("video_id", "").startswith("sha256:")
        ):
            if file_identity(source) != job["video_id"]:
                raise ValueError("Skedari ndryshoi pas shtimit në radhë; shtoje versionin e ri si punë të re")
        metadata = dict(metadata or {})
        if not job_source_allowed(rules, {**job, "metadata": metadata}):
            raise ValueError("Burimi i ruajtur nuk lejohet nga rregullat e fushatës")
        # A complete download survives disk pressure/restart without even contacting the provider again.
        temp_checkpoint = checkpoint.with_suffix(".tmp")
        temp_checkpoint.write_text(
            encode({"source": str(source.relative_to(self.store.config.root)), "metadata": metadata}),
            encoding="utf-8",
        )
        temp_checkpoint.replace(checkpoint)
        length = duration(source)
        if length < rules.min_seconds:
            raise ValueError(f"Videoja është më e shkurtër se {rules.min_seconds} sekonda")
        if not self.has_space(int(length * 90000)):
            self.store.execute(
                "UPDATE jobs SET metadata=? WHERE id=?",
                (encode({"reserve_bytes": int(length * 90000)}), job["id"]),
            )
            raise DownloadPaused("Shkarkimi u ruajt; përgatitja pret hapësirë për audio dhe segmente")
        audio = directory / "audio.wav"
        if not audio.exists():
            extract_audio(source, audio)
        chunk_seconds, overlap_seconds = chunking(settings, rules)
        metadata.update(
            {
                "duration": length,
                "source": str(source.relative_to(self.store.config.root)),
                "chunk_seconds": chunk_seconds,
                "overlap_seconds": overlap_seconds,
            }
        )
        self.store.execute(
            "UPDATE jobs SET title=?,metadata=?,stage='transcribe',updated=? WHERE id=?",
            (metadata.get("title", "VOD"), encode(metadata), time.time(), job["id"]),
        )
        step = chunk_seconds - overlap_seconds
        source_profile = self.store.one(
            "SELECT profile FROM sources WHERE id=?", (metadata.get("channel", "unknown"),)
        )
        speech = json.loads(source_profile["profile"]) if source_profile else {}
        for index, start in enumerate(range(0, int(length), step)):
            chunk_length = min(chunk_seconds, length - start)
            if chunk_length < rules.min_seconds or (index and chunk_length <= overlap_seconds):
                break  # The previous window already covers this tail completely.
            chunk = directory / f"chunk-{index}.wav"
            if not chunk.exists():
                audio_slice(audio, chunk, start, chunk_length)
            asset = self.store.add_asset(job["id"], chunk)
            self.store.add_task(
                job["id"],
                "transcribe",
                index,
                {
                    "offset": start,
                    "duration": chunk_length,
                    "audio_id": asset["id"],
                    "index": index,
                    "language": speech.get("language") or rules.language,
                    "vocabulary": speech.get("vocabulary", ""),
                },
                asset_ids=[asset["id"]],
            )
        self.store.execute(
            "UPDATE jobs SET status='processing',updated=? WHERE id=?", (time.time(), job["id"])
        )
        self.store.event("Videoja u përgatit për transkriptim", job_id=job["id"])

    def advance(self):
        self._proxies_left, self._renders_left = 2, 4
        jobs = self.store.all("SELECT * FROM jobs WHERE status='processing' ORDER BY created")
        # Deliver finished work across every job before spending CPU on new intermediates.
        for job in jobs:
            try:
                self.accept_renders(job)
            except Exception as exc:
                self.fail_job(job["id"], str(exc))
        for job in self.store.all("SELECT * FROM jobs WHERE status='processing' ORDER BY created"):
            try:
                self.advance_job(job)
            except Exception as exc:
                log.exception("Pipeline failed for %s", job["id"])
                self.fail_job(job["id"], str(exc))

    def advance_job(self, job):
        settings = self.store.settings()
        metadata = json.loads(job["metadata"])
        directory = self.store.config.root / "media" / job["id"]
        source = self.store.config.path(metadata["source"])
        failed = self.store.one(
            "SELECT kind,error FROM tasks WHERE job_id=? AND status='failed' AND (?!='automatic' OR kind IN ('transcribe','analyze')) LIMIT 1",
            (job["id"], job.get("review_mode", "manual")),
        )
        if failed:
            self.fail_job(job["id"], f"{failed['kind']}: {failed['error']}")
            return
        transcripts = self.store.all("SELECT * FROM tasks WHERE job_id=? AND kind='transcribe'", (job["id"],))
        for task in transcripts:
            if task["status"] != "complete":
                continue
            payload = json.loads(task["payload"])
            if self.store.one(
                "SELECT id FROM tasks WHERE job_id=? AND kind='analyze' AND task_key=?",
                (job["id"], task["task_key"]),
            ):
                continue
            prepared = self.store.one("""SELECT count(*) n FROM tasks t JOIN jobs j ON j.id=t.job_id
                WHERE t.kind='analyze' AND t.status IN ('queued','running') AND j.status='processing'""")["n"]
            if self._proxies_left <= 0 or prepared >= settings.max_prepared_tasks or not self.has_space():
                break
            proxy = directory / f"proxy-{payload['index']}.mp4"
            if not proxy.exists():
                make_proxy(source, proxy, payload["offset"], payload["duration"])
            asset = self.store.add_asset(job["id"], proxy)
            analysis_payload = {
                **payload,
                "proxy_id": asset["id"],
                "transcript": json.loads(task["result"]),
                "threshold": settings.score_threshold,
                "limits": analysis_limits(self.campaign_rules(job["id"])),
            }
            self.store.add_task(
                job["id"],
                "analyze",
                task["task_key"],
                analysis_payload,
                asset_ids=[asset["id"], payload["audio_id"]],
            )
            self._proxies_left -= 1
        analysis = self.store.all("SELECT * FROM tasks WHERE job_id=? AND kind='analyze'", (job["id"],))
        if transcripts and all(t["status"] == "complete" for t in transcripts):
            self.store.execute(
                "UPDATE jobs SET stage='analyze' WHERE id=? AND stage='transcribe'", (job["id"],)
            )
        if (
            len(analysis) == len(transcripts)
            and analysis
            and all(t["status"] == "complete" for t in analysis)
            and job["stage"] in {"transcribe", "analyze"}
        ):
            self.plan_clips(job, analysis)
        self.prepare_renders(job, source)
        pending = self.store.one(
            "SELECT count(*) n FROM clips WHERE job_id=? AND status NOT IN ('ready','skipped','technical_failed')",
            (job["id"],),
        )["n"]
        fresh = self.store.one("SELECT stage FROM jobs WHERE id=?", (job["id"],))
        if fresh["stage"] == "render" and pending == 0:
            self.store.execute(
                "UPDATE jobs SET status='complete',stage='complete',completed=?,updated=? WHERE id=?",
                (time.time(), time.time(), job["id"]),
            )
            count = self.store.one("SELECT count(*) n FROM clips WHERE job_id=?", (job["id"],))["n"]
            self.store.event(
                (
                    "Prova përfundoi; shiko raportin dhe klipet, pa publikim"
                    if job.get("run_mode") == "trial"
                    else "Përpunimi përfundoi; klipet kaluan në radhën e publikimit"
                )
                if count
                else "Nuk u gjetën momente që kalojnë pragun e cilësisë",
                job_id=job["id"],
            )

    def has_space(self, reserve_bytes=0):
        return (
            shutil.disk_usage(self.store.config.root).free
            >= self.store.settings().min_free_gb * 1024**3 + reserve_bytes
        )

    def plan_clips(self, job, analysis):
        threshold = self.store.settings().score_threshold
        campaign = self.campaign_rules(job["id"])
        rules, limits = clip_rules(campaign), analysis_limits(campaign) if campaign else None
        candidates, banned, incomplete = [], 0, 0
        for task in analysis:
            payload, result = json.loads(task["payload"]), json.loads(task["result"])
            for item in result["candidates"]:
                if item["score"] < threshold or not within(item, limits):
                    continue
                # Recheck on the main too: older analysis workers may propose a trailing sentence.
                item = refine_boundaries(
                    item,
                    payload["transcript"]["segments"],
                    payload["duration"],
                    rules.min_seconds,
                    rules.max_seconds,
                )
                if item is None:
                    incomplete += 1
                    continue
                candidate = dict(item)
                candidate["start"] += payload["offset"]
                candidate["end"] += payload["offset"]
                candidate["segments"] = relative_segments(
                    payload["transcript"]["segments"], item["start"], item["end"]
                )
                candidate["source_context"] = [
                    {
                        **seg,
                        "start": seg["start"] + payload["offset"],
                        "end": seg["end"] + payload["offset"],
                        "words": [
                            {
                                **w,
                                "start": w["start"] + payload["offset"],
                                "end": w["end"] + payload["offset"],
                            }
                            for w in seg.get("words", [])
                        ],
                    }
                    for seg in payload["transcript"]["segments"]
                    if seg["end"] >= item["start"] - 8 and seg["start"] <= item["end"] + 8
                ]
                candidate["source_duration"] = json.loads(job["metadata"]).get("duration", candidate["end"])
                candidate["editorial_evidence"] = boundary_evidence(
                    candidate["source_context"], candidate["start"], candidate["end"]
                )
                # A model cannot be trusted to honour a word ban; the transcript can be checked exactly.
                if banned_hits(
                    " ".join((spoken_text(candidate), candidate["title"], candidate.get("caption", ""))),
                    rules.banned_words,
                ):
                    banned += 1
                    continue
                candidates.append(candidate)
        candidates = select_candidates(candidates)
        if job.get("run_mode") == "trial":
            candidates = candidates[: job["trial_limit"]]
        if rules.max_clips_per_video:
            candidates = candidates[: rules.max_clips_per_video]  # Already ordered best first.
        if banned:
            self.store.event(f"{banned} momente u hoqën për fjalë të ndaluara nga fushata", job_id=job["id"])
        if incomplete:
            self.store.event(
                f"{incomplete} momente u hoqën sepse nuk kishin prerje të plotë brenda kufirit të kohës",
                job_id=job["id"],
            )
        targets = json.loads(job["group_ids"])
        with self.store.connect(immediate=True) as db:
            # Single transaction makes replay after a process crash harmless.
            current = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if current["stage"] == "render":
                return
            groups = [
                dict(row)
                for row in db.execute("""SELECT g.*,
                (SELECT count(*) FROM clips c WHERE c.group_id=g.id) n FROM groups g ORDER BY n,g.created,g.id""")
                if row["id"] in targets
            ]
            if not groups:
                raise ValueError("Nuk ka grupe destinacion për këtë punë")
            for candidate in candidates:
                group = min(groups, key=lambda g: (g["n"], g["id"]))
                now = time.time()
                db.execute(
                    """INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,created,updated)
                              VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        uid(),
                        job["id"],
                        group["id"],
                        candidate["start"],
                        candidate["end"],
                        candidate["score"],
                        encode(candidate),
                        now,
                        now,
                    ),
                )
                group["n"] += 1
            db.execute("UPDATE jobs SET stage='render',updated=? WHERE id=?", (time.time(), job["id"]))

    def prepare_renders(self, job, source):
        settings = self.store.settings()
        source_meta = json.loads(job["metadata"])
        profile = self.store.one(
            "SELECT profile FROM sources WHERE id=?", (source_meta.get("channel", "unknown"),)
        )
        profile = json.loads(profile["profile"]) if profile else {"mode": "auto"}
        campaign = self.campaign_rules(job["id"])
        overlay_rules = render_rules(campaign) if campaign else None
        if overlay_rules and not profile.get("language"):
            profile = {**profile, "language": clip_rules(campaign).language}
        self.accept_renders(job)
        for clip in self.store.all(
            "SELECT * FROM clips WHERE job_id=? AND status='preparing' ORDER BY score DESC LIMIT 4",
            (job["id"],),
        ):
            prepared = self.store.one("""SELECT count(*) n FROM clips c JOIN jobs j ON j.id=c.job_id
                WHERE c.status IN ('rendering','checking','correcting','approved') AND j.status='processing'""")[
                "n"
            ]
            if self._renders_left <= 0 or prepared >= settings.max_prepared_tasks or not self.has_space():
                return
            target = (
                self.store.config.root
                / "media"
                / job["id"]
                / f"segment-{clip['id']}-v{clip['current_version']}.mp4"
            )
            if not target.exists():
                cut_segment(source, target, clip["start"], clip["end"] - clip["start"])
            asset = self.store.add_asset(job["id"], target)
            group = self.store.one("SELECT * FROM groups WHERE id=?", (clip["group_id"],))
            clip_metadata = json.loads(clip["metadata"])
            editing = settings.editing
            if clip_metadata.get("top_title_override"):
                editing = editing.model_copy(update={"title_enabled": True, "title_text": ""})
            payload = {
                **clip_metadata,
                "clip_id": clip["id"],
                "version": clip["current_version"],
                "group_id": clip["group_id"],
                "segment_id": asset["id"],
                "profile": profile,
                "caption_style": settings.caption_style,
                "editing": editing.render_options(),
                "render_revision": editing.required_revision,
            }
            inputs = [asset["id"]]
            if overlay_rules:
                payload["rules"] = overlay_rules
                if overlay_rules["logo_sha256"]:
                    payload["logo_id"] = self.logo_asset(job["id"], overlay_rules["logo_sha256"])
                    inputs.append(payload["logo_id"])
            task_id = self.store.add_task(
                job["id"],
                "render",
                f"{clip['id']}:v{clip['current_version']}"
                if job.get("review_mode") == "automatic"
                else clip["id"],
                payload,
                group["preferred_worker"],
                inputs,
            )
            self.store.execute(
                "INSERT OR IGNORE INTO clip_versions(clip_id,version,metadata,render_task_id,created) VALUES (?,?,?,?,?)",
                (clip["id"], clip["current_version"], clip["metadata"], task_id, time.time()),
            )
            self.store.execute(
                "UPDATE clips SET status='rendering',updated=? WHERE id=?", (time.time(), clip["id"])
            )
            self._renders_left -= 1

    def logo_asset(self, job_id, digest):
        """Per-job copy of the verified campaign logo, so the existing leased-asset transfer delivers it."""
        from boti.campaigns import logo_path

        original = logo_path(self.store.config, digest)
        if not original.exists():
            raise ValueError("Logoja e fushatës mungon në main; ngarkoje përsëri te fushata")
        target = self.store.config.root / "media" / job_id / f"logo-{digest[:16]}.png"
        if not target.exists():
            shutil.copyfile(original, target)
        return self.store.add_asset(job_id, target)["id"]

    def accept_renders(self, job):
        from boti.campaigns import campaign_for_job

        campaign = campaign_for_job(self.store, job["id"])
        platforms = (
            json.loads(campaign["rules_snapshot"])["platforms"]
            if campaign
            else ["youtube", "instagram", "tiktok", "x"]
        )
        for task in self.store.all(
            "SELECT * FROM tasks WHERE job_id=? AND kind='render' AND status='complete'", (job["id"],)
        ):
            payload, result = json.loads(task["payload"]), json.loads(task["result"])
            clip = self.store.one("SELECT * FROM clips WHERE id=?", (payload["clip_id"],))
            if job.get("review_mode") == "automatic":
                version = payload.get("version", 1)
                if clip["current_version"] == version and clip["status"] == "rendering":
                    with self.store.connect(immediate=True) as db:
                        db.execute(
                            "UPDATE clip_versions SET asset_id=? WHERE clip_id=? AND version=?",
                            (result["asset_id"], clip["id"], version),
                        )
                        db.execute(
                            "UPDATE clips SET asset_id=?,status='checking',updated=? WHERE id=?",
                            (result["asset_id"], time.time(), clip["id"]),
                        )
            elif clip["status"] != "ready":
                self.deliver_clip(clip, result["asset_id"], platforms)
                self.release_render_input(task, payload)
            else:
                self.release_render_input(task, payload)
        if job.get("review_mode") == "automatic":
            from boti.quality import AutomaticReview

            AutomaticReview(self.store).tick(job)
            for clip in self.store.all(
                "SELECT * FROM clips WHERE job_id=? AND status='approved'", (job["id"],)
            ):
                self.deliver_clip(clip, clip["asset_id"], platforms)
            for task in self.store.all(
                "SELECT * FROM tasks WHERE job_id=? AND kind='render' AND status='complete'", (job["id"],)
            ):
                payload = json.loads(task["payload"])
                clip = self.store.one("SELECT status FROM clips WHERE id=?", (payload["clip_id"],))
                if clip["status"] in {"ready", "skipped", "technical_failed"}:
                    self.release_render_input(task, payload)

    def deliver_clip(self, clip, asset_id, platforms):
        job = self.store.one("SELECT run_mode FROM jobs WHERE id=?", (clip["job_id"],))
        trial = job["run_mode"] == "trial"
        if "tiktok" in platforms and not trial:
            self.export_tiktok(clip, asset_id)
        with self.store.connect(immediate=True) as db:
            db.execute(
                "UPDATE clips SET status='ready',asset_id=?,updated=? WHERE id=?",
                (asset_id, time.time(), clip["id"]),
            )
            if trial:
                return  # Trial output stays local; even connected accounts get no outbox entry.
            for account in db.execute(
                "SELECT * FROM accounts WHERE group_id=?", (clip["group_id"],)
            ).fetchall():
                if account["platform"] not in platforms:
                    continue
                db.execute(
                    """INSERT OR IGNORE INTO publications
                    (id,clip_id,account_id,platform,status,created,updated) VALUES (?,?,?,?,?,?,?)""",
                    (
                        uid(),
                        clip["id"],
                        account["id"],
                        account["platform"],
                        "manual_ready"
                        if account["platform"] == "tiktok" and account["status"] != "connected"
                        else "queued",
                        time.time(),
                        time.time(),
                    ),
                )

    def release_render_input(self, task, payload):
        # Keep the final output and original VOD; discard the redundant prepared segment.
        # Repeating this after a crash is safe because publication/export is already durable.
        asset = self.store.one(
            "SELECT * FROM assets WHERE id=? AND task_id IS NULL", (payload.get("segment_id"),)
        )
        if not asset:
            return
        active = self.store.one(
            """SELECT count(*) n FROM task_assets ta JOIN tasks t ON t.id=ta.task_id
            WHERE ta.asset_id=? AND t.id!=? AND t.status IN ('queued','running')""",
            (asset["id"], task["id"]),
        )
        if active["n"] == 0:
            try:
                self.store.config.path(asset["path"]).unlink(missing_ok=True)
            except OSError:
                log.warning("Segmenti i përfunduar nuk u pastrua ende: %s", asset["id"])

    def export_tiktok(self, clip, asset_id):
        target = self.store.config.root / "exports" / clip["group_id"] / clip["id"]
        target.mkdir(parents=True, exist_ok=True)
        asset = self.store.one("SELECT * FROM assets WHERE id=?", (asset_id,))
        source = self.store.config.path(asset["path"])
        video = target / "video.mp4"
        if not video.exists():
            try:
                video.hardlink_to(source)
            except OSError:
                shutil.copyfile(source, video)
        meta = json.loads(clip["metadata"])
        campaign = self.campaign_rules(clip["job_id"])
        (target / "caption.txt").write_text(
            caption(meta, clip_rules(campaign) if campaign else None, "tiktok"), encoding="utf-8"
        )
        (target / "metadata.json").write_text(
            json.dumps(
                {
                    "clip_id": clip["id"],
                    "group_id": clip["group_id"],
                    "title": meta["title"],
                    "score": clip["score"],
                    "source_start": clip["start"],
                    "source_end": clip["end"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def cleanup(self):
        settings, now = self.store.settings(), time.time()
        for pattern in ("media/**/*.part", "media/**/*.part.*", "exports/download-*.zip"):
            for path in self.store.config.root.glob(pattern):
                if path.stat().st_mtime < now - 86400:
                    if pattern.startswith("media/"):
                        parts = path.relative_to(self.store.config.root).parts
                        active = self.store.one(
                            "SELECT 1 FROM jobs WHERE id=? AND status NOT IN ('complete','failed')",
                            (parts[1],),
                        )
                        if active:
                            continue  # A download paused for disk pressure still owns its resume files.
                    path.unlink(missing_ok=True)
        for job in self.store.all(
            "SELECT * FROM jobs WHERE status='complete' AND completed<?",
            (now - settings.original_retention_days * 86400,),
        ):
            metadata = json.loads(job["metadata"])
            if metadata.get("originals_cleaned"):
                continue
            for asset in self.store.all(
                "SELECT * FROM assets WHERE job_id=? AND task_id IS NULL", (job["id"],)
            ):
                self.store.config.path(asset["path"]).unlink(missing_ok=True)
            directory = self.store.config.root / "media" / job["id"]
            for pattern in ("source.*", "trial.mp4", "trial-source.json", "trial-download.*"):
                for path in directory.glob(pattern):
                    path.unlink(missing_ok=True)
            (directory / "audio.wav").unlink(missing_ok=True)
            metadata["originals_cleaned"] = True
            self.store.execute("UPDATE jobs SET metadata=? WHERE id=?", (encode(metadata), job["id"]))
        # Retain report/version records, but bound disk used by rejected superseded renders.
        for asset in self.store.all(
            """SELECT a.* FROM clip_versions v JOIN assets a ON a.id=v.asset_id
                JOIN clips c ON c.id=v.clip_id JOIN jobs j ON j.id=c.job_id
                WHERE j.status='complete' AND v.created<? AND (
                  (c.asset_id!=a.id AND c.status IN ('ready','archived','skipped','technical_failed')) OR
                  (c.status IN ('skipped','technical_failed') AND NOT EXISTS
                    (SELECT 1 FROM publications p WHERE p.clip_id=c.id)))""",
            (now - settings.output_retention_days * 86400,),
        ):
            self.store.config.path(asset["path"]).unlink(missing_ok=True)
        for clip in self.store.all(
            "SELECT * FROM clips WHERE status='ready' AND updated<?",
            (now - settings.output_retention_days * 86400,),
        ):
            if self.store.one(
                """SELECT 1 FROM job_campaigns jc WHERE jc.job_id=? AND EXISTS (
                SELECT 1 FROM publications p LEFT JOIN submissions s ON s.publication_id=p.id
                WHERE p.clip_id=? AND coalesce(s.status,'ready')!='paid')""",
                (clip["job_id"], clip["id"]),
            ):
                continue
            pending = self.store.one(
                "SELECT count(*) n FROM publications WHERE clip_id=? AND status!='published'", (clip["id"],)
            )
            if pending["n"] == 0:
                asset = self.store.one("SELECT * FROM assets WHERE id=?", (clip["asset_id"],))
                self.store.config.path(asset["path"]).unlink(missing_ok=True)
                shutil.rmtree(
                    self.store.config.root / "exports" / clip["group_id"] / clip["id"], ignore_errors=True
                )
                self.store.execute("UPDATE clips SET status='archived' WHERE id=?", (clip["id"],))


def relative_segments(segments, start, end):
    result = []
    for segment in segments:
        if segment["end"] <= start or segment["start"] >= end:
            continue
        words = [
            {**word, "start": max(0, word["start"] - start), "end": min(end - start, word["end"] - start)}
            for word in segment.get("words", [])
            if word["end"] > start and word["start"] < end
        ]
        result.append(
            {
                "start": max(0, segment["start"] - start),
                "end": min(end - start, segment["end"] - start),
                "text": " ".join(word["word"].strip() for word in words)
                if words
                else (segment["text"] if segment["start"] >= start and segment["end"] <= end else ""),
                "words": words,
            }
        )
    return result


def caption(meta, rules=None, platform=None):
    """Post text. With campaign rules it always carries the required tags, mentions and disclosure."""
    if rules is None:
        tags = " ".join("#" + tag.lstrip("#") for tag in meta.get("hashtags", []) if tag)
        return "\n\n".join(part for part in (meta.get("caption") or meta["title"], tags) if part)
    return final_caption(meta, rules, platform)

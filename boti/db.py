from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from boti.config import Config, Settings, private_write
from boti.routing import eligible_capabilities


def uid() -> str:
    return uuid.uuid4().hex


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workers (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL,
 capabilities TEXT NOT NULL DEFAULT '[]', hardware TEXT NOT NULL DEFAULT '{}',
 last_seen REAL, paused INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS groups (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, preferred_worker TEXT REFERENCES workers(id),
 paused INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
 id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES groups(id),
 platform TEXT NOT NULL CHECK(platform IN ('youtube','instagram','tiktok')),
 external_id TEXT, name TEXT, credentials TEXT, settings TEXT NOT NULL DEFAULT '{}',
 status TEXT NOT NULL DEFAULT 'disconnected', error TEXT,
 UNIQUE(group_id, platform)
);
CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, profile TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS account_destination ON accounts(platform,external_id) WHERE external_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL UNIQUE, url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'queued', stage TEXT NOT NULL DEFAULT 'download',
 group_ids TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', error TEXT,
 created REAL NOT NULL, updated REAL NOT NULL, completed REAL
);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), kind TEXT NOT NULL,
 task_key TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
 worker_id TEXT REFERENCES workers(id), preferred_worker TEXT REFERENCES workers(id),
 lease_token TEXT, lease_expires REAL, attempts INTEGER NOT NULL DEFAULT 0,
 not_before REAL NOT NULL DEFAULT 0, result TEXT, error TEXT,
 created REAL NOT NULL, updated REAL NOT NULL, started REAL, finished REAL,
 UNIQUE(job_id, kind, task_key)
);
CREATE INDEX IF NOT EXISTS task_queue ON tasks(status, not_before, created);
CREATE INDEX IF NOT EXISTS task_job ON tasks(job_id,kind,status);
CREATE TABLE IF NOT EXISTS assets (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
 task_id TEXT REFERENCES tasks(id), path TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
 size INTEGER NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS task_assets (
 task_id TEXT NOT NULL REFERENCES tasks(id), asset_id TEXT NOT NULL REFERENCES assets(id),
 PRIMARY KEY(task_id,asset_id)
);
CREATE TABLE IF NOT EXISTS clips (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), group_id TEXT NOT NULL REFERENCES groups(id),
 start REAL NOT NULL, end REAL NOT NULL, score REAL NOT NULL, metadata TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'preparing', asset_id TEXT REFERENCES assets(id),
 created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS publications (
 id TEXT PRIMARY KEY, clip_id TEXT NOT NULL REFERENCES clips(id), account_id TEXT NOT NULL REFERENCES accounts(id),
 platform TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', state TEXT NOT NULL DEFAULT '{}',
 attempts INTEGER NOT NULL DEFAULT 0, not_before REAL NOT NULL DEFAULT 0,
 external_id TEXT, url TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL,
 UNIQUE(clip_id, account_id)
);
CREATE INDEX IF NOT EXISTS publication_queue ON publications(status,not_before);
CREATE INDEX IF NOT EXISTS publication_clip ON publications(clip_id,status);
CREATE INDEX IF NOT EXISTS clip_job ON clips(job_id,status);
CREATE INDEX IF NOT EXISTS clip_group ON clips(group_id);
CREATE INDEX IF NOT EXISTS job_queue ON jobs(status,created);
CREATE TABLE IF NOT EXISTS quota_events (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, publication_id TEXT NOT NULL,
 created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT NOT NULL, message TEXT NOT NULL,
 job_id TEXT, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
 clip_id TEXT PRIMARY KEY REFERENCES clips(id), passed INTEGER NOT NULL,
 notes TEXT NOT NULL DEFAULT '', updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS quota_project ON quota_events(project_id,created);
CREATE INDEX IF NOT EXISTS assets_job ON assets(job_id);
"""


class Store:
    def __init__(self, config: Config):
        from boti.campaigns import SCHEMA as CAMPAIGN_SCHEMA
        from boti.feeds import SCHEMA as FEED_SCHEMA

        self.config = config
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.executescript(CAMPAIGN_SCHEMA)
            db.executescript(FEED_SCHEMA)
            db.execute("INSERT OR IGNORE INTO settings VALUES ('config', ?)", (Settings().model_dump_json(),))
            from boti.quality import migrate

            migrate(db)
            from boti.trials import migrate as migrate_trials

            migrate_trials(db)
        admin_file = config.root / "secrets" / "admin.token"
        if not admin_file.exists():
            private_write(admin_file, secrets.token_urlsafe(32))
        self.admin_hash = token_hash(admin_file.read_text().strip())

    @contextmanager
    def connect(self, immediate=False):
        db = sqlite3.connect(self.config.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            if immediate:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def all(self, sql, args=()) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    def one(self, sql, args=()) -> dict | None:
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.connect() as db:
            return db.execute(sql, args).rowcount

    def settings(self) -> Settings:
        return Settings.model_validate_json(
            self.one("SELECT value FROM settings WHERE key='config'")["value"]
        )

    def save_settings(self, settings: Settings):
        self.execute("UPDATE settings SET value=? WHERE key='config'", (settings.model_dump_json(),))

    def event(self, message, level="info", job_id=None):
        self.execute(
            "INSERT INTO events(level,message,job_id,created) VALUES (?,?,?,?)",
            (level, message[:2000], job_id, time.time()),
        )

    def seed(self, coordinator_url=None) -> list[Path]:
        paths = []
        for index in range(1, 5):
            worker_id = f"pc-{index}"
            if not self.one("SELECT id FROM workers WHERE id=?", (worker_id,)):
                token = secrets.token_urlsafe(32)
                self.execute(
                    "INSERT INTO workers(id,name,token_hash) VALUES (?,?,?)",
                    (worker_id, f"PC {index}", token_hash(token)),
                )
                path = self.config.root / "secrets" / f"worker-{index}.json"
                private_write(
                    path,
                    json.dumps(
                        {
                            "id": worker_id,
                            "token": token,
                            "coordinator_url": coordinator_url or "http://127.0.0.1:8765",
                            "cache_dir": "worker-data",
                            "capabilities": ["transcribe", "analyze", "render", "quality", "repair"],
                            "ollama_url": "http://127.0.0.1:11434",
                            "model": "qwen3.5:4b",
                            "whisper_device": "cpu",
                            "whisper_model": "medium",
                            "encoder": "auto",
                            "verify_tls": True,
                        },
                        indent=2,
                    ),
                )
                paths.append(path)
            elif coordinator_url:
                path = self.config.root / "secrets" / f"worker-{index}.json"
                if path.exists():
                    content = json.loads(path.read_text())
                    content["coordinator_url"] = coordinator_url
                    private_write(path, json.dumps(content, indent=2))
                    paths.append(path)
            for group_number in range(1, 4):
                group_id = f"pc-{index}-g{group_number}"
                if not self.one("SELECT id FROM groups WHERE id=?", (group_id,)):
                    self.add_group(f"PC {index} · Grupi {group_number}", worker_id, group_id)
        return paths

    def add_group(self, name, preferred_worker=None, group_id=None):
        group_id = group_id or uid()
        with self.connect(immediate=True) as db:
            db.execute(
                "INSERT INTO groups(id,name,preferred_worker,created) VALUES (?,?,?,?)",
                (group_id, name, preferred_worker, time.time()),
            )
            for platform in ("youtube", "instagram", "tiktok"):
                db.execute(
                    "INSERT INTO accounts(id,group_id,platform,status) VALUES (?,?,?,?)",
                    (uid(), group_id, platform, "manual" if platform == "tiktok" else "disconnected"),
                )
        return group_id

    def create_job(
        self,
        url,
        source_id,
        groups,
        campaign_id=None,
        provider="twitch",
        review_mode="manual",
        metadata=None,
        trial=None,
    ):
        if provider not in {"twitch", "kick", "youtube", "file"} or review_mode not in {
            "automatic",
            "manual",
        }:
            raise ValueError("Invalid provider or review mode")
        video_id = source_id
        source_id = f"{provider}:{video_id}"
        run_mode, trial_start, trial_seconds, trial_limit = "production", 0, 0, 0
        if trial is not None:
            from boti.models import TrialOptions

            options = TrialOptions.model_validate(trial)
            run_mode, trial_start, trial_seconds, trial_limit = (
                "trial",
                options.start_seconds,
                options.duration_seconds,
                options.max_clips,
            )
            review_mode = "automatic"
            source_id += f":trial:{trial_start}:{trial_seconds}:{trial_limit}"
        with self.connect(immediate=True) as db:
            existing = db.execute("SELECT id FROM jobs WHERE source_id=?", (source_id,)).fetchone()
            if existing:
                linked = db.execute(
                    "SELECT campaign_id FROM job_campaigns WHERE job_id=?", (existing["id"],)
                ).fetchone()
                if (linked["campaign_id"] if linked else None) != campaign_id:
                    raise ValueError("Ky VOD është caktuar tashmë; fushata nuk ndryshohet në heshtje")
                return existing["id"], False
            campaign = None
            if campaign_id:
                from boti.campaigns import Campaign

                campaign = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                if not campaign:
                    raise ValueError("Fushata nuk ekziston")
                rules = Campaign.model_validate_json(campaign["data"])
                if trial is not None and trial_seconds < rules.clip.min_seconds:
                    raise ValueError(
                        "Intervali i provës është më i shkurtër se minimumi i klipit të fushatës"
                    )
                if rules.status in {"paused", "closed"} or (rules.deadline and rules.deadline <= time.time()):
                    raise ValueError("Fushata është pezulluar, mbyllur ose skaduar")
                if not groups or not set(groups).issubset(rules.group_ids):
                    raise ValueError("Zgjidh vetëm grupet e caktuara për fushatën")
                if rules.max_accounts_per_platform and len(groups) > rules.max_accounts_per_platform:
                    raise ValueError("Grupet tejkalojnë kufirin e llogarive për platformë të fushatës")
            job_id = uid()
            now = time.time()
            db.execute(
                "INSERT INTO jobs(id,source_id,url,group_ids,metadata,created,updated,provider,video_id,review_mode,run_mode,trial_start,trial_seconds,trial_limit) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    source_id,
                    url,
                    encode(groups),
                    encode(metadata or {}),
                    now,
                    now,
                    provider,
                    video_id,
                    review_mode,
                    run_mode,
                    trial_start,
                    trial_seconds,
                    trial_limit,
                ),
            )
            if campaign:
                db.execute(
                    "INSERT INTO job_campaigns VALUES (?,?,?,?)",
                    (job_id, campaign_id, campaign["revision"], campaign["data"]),
                )
        return job_id, True

    def add_task(self, job_id, kind, key, payload, preferred=None, asset_ids=()):
        task_id = uid()
        now = time.time()
        with self.connect(immediate=True) as db:
            existing = db.execute(
                "SELECT id FROM tasks WHERE job_id=? AND kind=? AND task_key=?", (job_id, kind, str(key))
            ).fetchone()
            if existing:
                return existing["id"]
            db.execute(
                """INSERT INTO tasks(id,job_id,kind,task_key,payload,preferred_worker,created,updated)
                          VALUES (?,?,?,?,?,?,?,?)""",
                (task_id, job_id, kind, str(key), encode(payload), preferred, now, now),
            )
            db.executemany("INSERT INTO task_assets VALUES (?,?)", [(task_id, a) for a in asset_ids])
        return task_id

    def add_asset(self, job_id, path: Path, task_id=None):
        from boti.media import sha256

        relative = str(path.resolve().relative_to(self.config.root))
        existing = self.one("SELECT * FROM assets WHERE path=?", (relative,))
        if existing:
            return existing
        asset = {
            "id": uid(),
            "job_id": job_id,
            "task_id": task_id,
            "path": relative,
            "sha256": sha256(path),
            "size": path.stat().st_size,
            "created": time.time(),
        }
        self.execute("INSERT INTO assets VALUES (:id,:job_id,:task_id,:path,:sha256,:size,:created)", asset)
        return asset

    def reap(self, now=None):
        now = time.time() if now is None else now
        with self.connect(immediate=True) as db:
            db.execute(
                """UPDATE tasks SET status=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END,
              worker_id=NULL, lease_token=NULL, lease_expires=NULL, updated=?, error='PC u shkëput; detyra ricaktohet'
              WHERE status='running' AND lease_expires<?""",
                (now, now),
            )

    def claim(self, worker_id):
        self.reap()
        now = time.time()
        settings = self.settings()
        if settings.paused:
            return None
        with self.connect(immediate=True) as db:
            worker = db.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
            if not worker or worker["paused"]:
                return None
            if db.execute(
                "SELECT id FROM tasks WHERE worker_id=? AND status='running'", (worker_id,)
            ).fetchone():
                return None
            capabilities = sorted(
                eligible_capabilities(
                    db.execute("SELECT id,capabilities,last_seen,paused FROM workers").fetchall(),
                    settings.analysis_worker_id,
                    now,
                ).get(worker_id, set())
            )
            if not capabilities:
                return None
            marks = ",".join("?" for _ in capabilities)
            row = db.execute(
                f"""SELECT t.* FROM tasks t JOIN jobs j ON j.id=t.job_id
                WHERE t.status='queued' AND t.not_before<=? AND t.attempts<3
                AND j.status='processing' AND t.kind IN ({marks})
                ORDER BY CASE WHEN t.created<? THEN 0 ELSE 1 END,
                CASE WHEN t.created<? THEN t.created ELSE 0 END,
                CASE t.kind WHEN 'quality' THEN -1 WHEN 'repair' THEN -1 WHEN 'render' THEN 0 WHEN 'analyze' THEN 1 ELSE 2 END,
                CASE WHEN t.preferred_worker=? THEN 0 ELSE 1 END,t.created LIMIT 1""",
                (now, *capabilities, now - 1800, now - 1800, worker_id),
            ).fetchone()
            if not row:
                return None
            lease = secrets.token_urlsafe(24)
            db.execute(
                """UPDATE tasks SET status='running',worker_id=?,lease_token=?,lease_expires=?,
                attempts=attempts+1,updated=?,started=? WHERE id=?""",
                (worker_id, lease, now + settings.lease_seconds, now, now, row["id"]),
            )
            task = dict(db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())
            task["payload"] = json.loads(task["payload"])
            assets = db.execute(
                "SELECT a.* FROM assets a JOIN task_assets ta ON ta.asset_id=a.id WHERE ta.task_id=?",
                (task["id"],),
            ).fetchall()
            task["assets"] = [
                {"id": a["id"], "sha256": a["sha256"], "size": a["size"], "name": Path(a["path"]).name}
                for a in assets
            ]
        return task

    def lease(self, task_id, worker_id, token, now=None):
        now = time.time() if now is None else now
        row = self.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if (
            not row
            or row["status"] != "running"
            or row["worker_id"] != worker_id
            or not secrets.compare_digest(row["lease_token"] or "", token)
            or row["lease_expires"] < now
        ):
            raise ValueError("Caktimi skadoi ose detyra i takon një PC-je tjetër")
        return row

    def heartbeat(self, worker_id, capabilities=None, hardware=None, task_id=None, lease_token=None):
        now = time.time()
        with self.connect(immediate=True) as db:
            db.execute("UPDATE workers SET last_seen=? WHERE id=?", (now, worker_id))
            if capabilities is not None:
                db.execute(
                    "UPDATE workers SET capabilities=?,hardware=? WHERE id=?",
                    (encode(capabilities), encode(hardware or {}), worker_id),
                )
            if task_id:
                count = db.execute(
                    """UPDATE tasks SET lease_expires=?,updated=? WHERE id=? AND worker_id=?
                    AND lease_token=? AND status='running' AND lease_expires>=?""",
                    (now + self.settings().lease_seconds, now, task_id, worker_id, lease_token, now),
                ).rowcount
                if count != 1:
                    raise ValueError("Caktimi skadoi")

    def finish(self, task_id, worker_id, token, result=None, error=None, retryable=True):
        now = time.time()
        with self.connect(immediate=True) as db:
            task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if (
                not task
                or task["status"] != "running"
                or task["worker_id"] != worker_id
                or not secrets.compare_digest(task["lease_token"] or "", token)
                or task["lease_expires"] < now
            ):
                raise ValueError("Caktimi skadoi")
            status = (
                "complete"
                if error is None
                else ("queued" if retryable and task["attempts"] < 3 else "failed")
            )
            db.execute(
                """UPDATE tasks SET status=?,result=?,error=?,lease_token=NULL,lease_expires=NULL,
                not_before=?,updated=?,finished=? WHERE id=?""",
                (
                    status,
                    encode(result) if result is not None else None,
                    error,
                    now + min(300, 10 * 2 ** task["attempts"]) if error else 0,
                    now,
                    now,
                    task_id,
                ),
            )

    def snapshot(self):
        workers = self.all("SELECT id,name,capabilities,hardware,last_seen,paused FROM workers ORDER BY id")
        eligible = eligible_capabilities(workers, self.settings().analysis_worker_id)
        for worker in workers:
            worker["capabilities"] = json.loads(worker["capabilities"])
            worker["assigned_capabilities"] = sorted(eligible[worker["id"]])
            worker["hardware"] = json.loads(worker["hardware"])
            worker["task"] = self.one(
                "SELECT id,kind,job_id,started FROM tasks WHERE worker_id=? AND status='running'",
                (worker["id"],),
            )
        groups = self.all("SELECT * FROM groups ORDER BY created,id")
        for group in groups:
            group["accounts"] = self.all(
                "SELECT id,platform,external_id,name,status,error FROM accounts WHERE group_id=?",
                (group["id"],),
            )
        counts = {
            r["status"]: r["n"] for r in self.all("SELECT status,count(*) n FROM clips GROUP BY status")
        }
        return {
            "workers": workers,
            "groups": groups,
            "settings": self.settings().model_dump(),
            "counts": counts,
            "stages": self.stage_metrics(),
            "job_counts": self.one(
                "SELECT count(*) total,coalesce(sum(status NOT IN ('complete','failed')),0) pending FROM jobs"
            ),
            "publication_counts": self.all(
                "SELECT platform,status,count(*) n FROM publications GROUP BY platform,status"
            ),
            "jobs": self.all("""SELECT j.*,
                    (SELECT count(*) FROM tasks t WHERE t.job_id=j.id AND t.status='complete') completed_tasks,
                    (SELECT count(*) FROM tasks t WHERE t.job_id=j.id) total_tasks,
                    (SELECT count(*) FROM clips c WHERE c.job_id=j.id) clip_count,
                    (SELECT count(*) FROM clips c WHERE c.job_id=j.id AND c.status IN ('ready','archived')) accepted_clips,
                    (SELECT count(*) FROM clips c WHERE c.job_id=j.id AND c.status='skipped') skipped_clips,
                    (SELECT count(*) FROM clips c WHERE c.job_id=j.id AND c.status='technical_failed') technical_failed_clips
                    FROM jobs j ORDER BY j.created DESC LIMIT 100"""),
            "publications": self.all("""SELECT p.*,a.name account_name,g.name group_name,c.metadata,
                    c.asset_id,c.score FROM publications p JOIN accounts a ON a.id=p.account_id
                    JOIN clips c ON c.id=p.clip_id JOIN groups g ON g.id=c.group_id
                    ORDER BY p.created DESC LIMIT 300"""),
            "events": self.all("SELECT * FROM events ORDER BY id DESC LIMIT 30"),
            "reviews": self.one("SELECT count(*) total,coalesce(sum(passed),0) passed FROM reviews"),
            "throughput": self.one(
                "SELECT count(*) clips_24h FROM clips WHERE status='ready' AND updated>?",
                (time.time() - 86400,),
            ),
        }

    def stage_metrics(self):
        now = time.time()
        eligible = eligible_capabilities(
            self.all("SELECT id,capabilities,last_seen,paused FROM workers"),
            self.settings().analysis_worker_id,
            now,
        )
        result = []
        for kind in ("transcribe", "analyze", "render", "quality", "repair"):
            queue = self.one(
                """SELECT
                coalesce(sum(t.status='queued'),0) queued,
                coalesce(sum(t.status='running'),0) running,
                min(CASE WHEN t.status='queued' THEN t.created END) oldest
                FROM tasks t JOIN jobs j ON j.id=t.job_id WHERE t.kind=? AND j.status='processing'
                AND t.status IN ('queued','running')""",
                (kind,),
            )
            recent = self.one(
                """SELECT count(*) samples,avg(finished-started) average_seconds,
                coalesce(sum(attempts>1),0) retried FROM tasks
                WHERE kind=? AND status='complete' AND finished>? AND started IS NOT NULL""",
                (kind, now - 86400),
            )
            capable = sum(kind in caps for caps in eligible.values())
            result.append(
                {
                    "kind": kind,
                    **queue,
                    **recent,
                    "capable_workers": capable,
                    "oldest_wait_seconds": max(0, now - queue["oldest"]) if queue["oldest"] else 0,
                }
            )
        return result

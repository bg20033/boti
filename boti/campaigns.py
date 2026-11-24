"""Local campaign records and manual submission tracking; no external service automation."""

from __future__ import annotations

import hashlib
import io
import json
import time
from decimal import Decimal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from boti.db import encode, uid
from boti.rules import ClipRules, clip_rules, compliance, parse_rules_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
 id TEXT PRIMARY KEY, data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS job_campaigns (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 revision INTEGER NOT NULL, rules_snapshot TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
 publication_id TEXT PRIMARY KEY REFERENCES publications(id),
 status TEXT NOT NULL, bounty TEXT NOT NULL DEFAULT '', reference TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '', updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS submission_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, publication_id TEXT NOT NULL REFERENCES publications(id),
 data TEXT NOT NULL, created REAL NOT NULL
);
"""


class Campaign(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(default="", max_length=1000)
    rules: str = Field(default="", max_length=15000)
    group_ids: list[str] = Field(default_factory=list, max_length=500)
    platforms: list[str] = Field(
        default_factory=lambda: ["youtube", "instagram", "tiktok", "x"], min_length=1, max_length=4
    )
    status: str = Field(default="draft", pattern="^(draft|active|paused|closed)$")
    payout_type: str = Field(default="payrate", pattern="^(payrate|pot|bounty)$")
    rate: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=6)
    rate_unit: int = Field(default=100000)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    min_post_views: int | None = Field(default=None, ge=0)
    min_campaign_views: int | None = Field(default=None, ge=0)
    max_accounts_per_platform: int | None = Field(default=None, ge=1)
    deadline: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    rules_confirmed: bool = False
    automation_confirmed: bool = False
    clip: ClipRules = Field(default_factory=ClipRules)

    @model_validator(mode="after")
    def valid(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Emri nuk mund të jetë bosh")
        if self.url:
            p = urlparse(self.url)
            if (
                p.scheme != "https"
                or p.hostname not in {"clipping.net", "www.clipping.net"}
                or p.username
                or p.password
                or p.port
            ):
                raise ValueError("Përdor URL HTTPS të clipping.net")
        if self.rate_unit not in (1000, 100000):
            raise ValueError("Njësia duhet të jetë 1,000 ose 100,000 shikime")
        if any(p not in {"youtube", "instagram", "tiktok", "x"} for p in self.platforms):
            raise ValueError("Platformë e pambështetur")
        self.platforms = list(dict.fromkeys(self.platforms))
        self.group_ids = list(dict.fromkeys(self.group_ids))
        if self.rules_confirmed and not self.rules.strip():
            raise ValueError("Ruaj rregullat para konfirmimit")
        if self.max_accounts_per_platform and len(self.group_ids) > self.max_accounts_per_platform:
            raise ValueError("Grupet tejkalojnë kufirin e llogarive për platformë")
        return self


class Submission(BaseModel):
    status: str = Field(pattern="^(ready|submitted|tracking|stopped|rejected|paid)$")
    bounty: str = Field(default="", max_length=200)
    reference: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=2000)


def logo_path(config, digest):
    return config.root / "media" / "logos" / f"{digest}.png"


class RulesText(BaseModel):
    text: str = Field(min_length=1, max_length=15000)


def clip_compliance(store, clip, campaign=None):
    """Current clip version against the job's confirmed rules snapshot. Unlinked jobs have no rules."""
    linked = store.one("SELECT rules_snapshot FROM job_campaigns WHERE job_id=?", (clip["job_id"],))
    if not linked:
        return {"passed": True, "issues": []}
    snapshot = campaign if campaign is not None else json.loads(linked["rules_snapshot"])
    render = None
    version = store.one(
        "SELECT render_task_id FROM clip_versions WHERE clip_id=? AND version=?",
        (clip["id"], clip.get("current_version", 1)),
    )
    if version and version["render_task_id"] and clip.get("asset_id"):
        task = store.one(
            "SELECT result FROM tasks WHERE id=? AND status='complete'", (version["render_task_id"],)
        )
        if task and task["result"]:
            render = json.loads(task["result"])
    meta = json.loads(clip["metadata"]) if isinstance(clip["metadata"], str) else clip["metadata"]
    return compliance(meta, clip_rules(snapshot), clip["end"] - clip["start"], render)


def campaign_for_job(store, job_id):
    return store.one(
        """SELECT jc.*,c.data current_rules,c.revision current_revision
        FROM job_campaigns jc JOIN campaigns c ON c.id=jc.campaign_id WHERE jc.job_id=?""",
        (job_id,),
    )


def publish_allowed(store, clip):
    from boti.quality import automatic_passed

    review = store.one("SELECT passed FROM reviews WHERE clip_id=?", (clip["id"],))
    if review and not review["passed"]:
        return False
    clip = store.one("SELECT * FROM clips WHERE id=?", (clip["id"],)) or clip
    if clip["status"] == "deleted":
        return False
    job = store.one("SELECT * FROM jobs WHERE id=?", (clip["job_id"],))
    if job and job["run_mode"] == "trial":
        return False
    automatic = job and job["review_mode"] == "automatic"
    if automatic and not automatic_passed(store, clip):
        return False
    linked = campaign_for_job(store, clip["job_id"])
    if not linked:
        return True
    rules = Campaign.model_validate_json(linked["current_rules"])
    from boti.rules import job_source_allowed

    if not job_source_allowed(rules.clip, job):
        return False
    return (
        rules.status == "active"
        and rules.rules_confirmed
        and rules.automation_confirmed
        and linked["revision"] == linked["current_revision"]
        and clip["group_id"] in rules.group_ids
        and (rules.deadline is None or rules.deadline > time.time())
        and (automatic or bool(review and review["passed"]))
        and clip_compliance(store, clip)["passed"]
    )


def routes(store, admin):
    router = APIRouter(prefix="/api", dependencies=[Depends(admin)])

    @router.get("/campaigns")
    def campaigns():
        return [
            {**row, "data": json.loads(row["data"])}
            for row in store.all("SELECT * FROM campaigns ORDER BY created DESC")
        ]

    def validate_groups(body):
        if any(not store.one("SELECT id FROM groups WHERE id=?", (g,)) for g in body.group_ids):
            raise HTTPException(422, "Grupi nuk ekziston")

    @router.post("/campaigns/parse-rules")
    def parse(body: RulesText):
        return parse_rules_text(body.text)

    @router.post("/campaigns")
    def create(body: Campaign):
        validate_groups(body)
        body.clip.logo_sha256 = ""  # A logo is attached only through the verified upload route.
        identity, now = uid(), time.time()
        store.execute(
            "INSERT INTO campaigns(id,data,created,updated) VALUES (?,?,?,?)",
            (identity, body.model_dump_json(), now, now),
        )
        return {"id": identity}

    @router.put("/campaigns/{identity}")
    def update(identity: str, body: Campaign):
        validate_groups(body)
        with store.connect(immediate=True) as db:
            old = db.execute("SELECT data FROM campaigns WHERE id=?", (identity,)).fetchone()
            if not old:
                raise HTTPException(404)
            # Compare normalised documents so pre-0.5 rows do not gain a revision from new default fields.
            body.clip.logo_sha256 = Campaign.model_validate_json(old["data"]).clip.logo_sha256
            if Campaign.model_validate_json(old["data"]) != body:
                db.execute(
                    "UPDATE campaigns SET data=?,revision=revision+1,updated=? WHERE id=?",
                    (body.model_dump_json(), time.time(), identity),
                )
        return {"ok": True}

    def set_logo(identity, digest):
        with store.connect(immediate=True) as db:
            old = db.execute("SELECT data FROM campaigns WHERE id=?", (identity,)).fetchone()
            if not old:
                raise HTTPException(404)
            campaign = Campaign.model_validate_json(old["data"])
            if campaign.clip.logo_sha256 != digest:
                campaign.clip.logo_sha256 = digest
                db.execute(
                    "UPDATE campaigns SET data=?,revision=revision+1,updated=? WHERE id=?",
                    (campaign.model_dump_json(), time.time(), identity),
                )
        return {"ok": True, "logo_sha256": digest}

    @router.put("/campaigns/{identity}/logo")
    async def upload_logo(identity: str, request: Request):
        """Store the campaign's own logo, re-encoded to PNG so workers never parse an untrusted upload."""
        from PIL import Image, UnidentifiedImageError

        if not store.one("SELECT id FROM campaigns WHERE id=?", (identity,)):
            raise HTTPException(404)
        size = request.headers.get("content-length", "")
        if not size.isdigit() or not 1 <= int(size) <= 2 * 1024 * 1024:
            raise HTTPException(413, "Logoja duhet të jetë nën 2 MB")
        data = await request.body()
        if len(data) > 2 * 1024 * 1024:
            raise HTTPException(413, "Logoja duhet të jetë nën 2 MB")
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 16_000_000:
                    raise HTTPException(422, "Përdor PNG, JPEG ose WebP deri në 16 megapikselë")
                image = image.convert("RGBA")
                image.thumbnail((800, 800))
                buffer = io.BytesIO()
                image.save(buffer, "PNG")
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise HTTPException(422, "Skedari nuk është imazh i vlefshëm") from exc
        digest = hashlib.sha256(buffer.getvalue()).hexdigest()
        path = logo_path(store.config, digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".part")
            temp.write_bytes(buffer.getvalue())
            temp.replace(path)
        return set_logo(identity, digest)

    @router.delete("/campaigns/{identity}/logo")
    def remove_logo(identity: str):
        return set_logo(identity, "")

    @router.get("/campaigns/{identity}/logo")
    def show_logo(identity: str):
        row = store.one("SELECT data FROM campaigns WHERE id=?", (identity,))
        digest = Campaign.model_validate_json(row["data"]).clip.logo_sha256 if row else ""
        if not digest or not logo_path(store.config, digest).exists():
            raise HTTPException(404)
        return FileResponse(logo_path(store.config, digest), media_type="image/png")

    @router.get("/submissions")
    def submissions(limit: int = 100, offset: int = 0):
        if not 1 <= limit <= 200 or offset < 0:
            raise HTTPException(422)
        rows = store.all(
            """SELECT p.id,p.url,p.platform,p.status publication_status,c.id clip_id,c.metadata,
            g.name group_name,ca.data campaign_data,jc.campaign_id,
            coalesce(s.status,'ready') status,s.bounty,s.reference,s.notes,s.updated
            FROM publications p JOIN clips c ON c.id=p.clip_id JOIN groups g ON g.id=c.group_id
            JOIN job_campaigns jc ON jc.job_id=c.job_id JOIN campaigns ca ON ca.id=jc.campaign_id
            LEFT JOIN submissions s ON s.publication_id=p.id
            WHERE p.status='published' ORDER BY p.created DESC,p.id LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        for row in rows:
            row["title"] = json.loads(row.pop("metadata")).get("title", "Klip")
            row["campaign_name"] = json.loads(row.pop("campaign_data"))["name"]
        return rows

    @router.post("/jobs/{job_id}/campaign-review")
    def review_rules(job_id: str):
        with store.connect(immediate=True) as db:
            linked = db.execute(
                """SELECT jc.*,c.data,c.revision latest FROM job_campaigns jc
                JOIN campaigns c ON c.id=jc.campaign_id WHERE job_id=?""",
                (job_id,),
            ).fetchone()
            if not linked:
                raise HTTPException(404)
            groups = json.loads(db.execute("SELECT group_ids FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
            rules = Campaign.model_validate_json(linked["data"])
            if not set(groups).issubset(rules.group_ids):
                raise HTTPException(409, "Destinacionet ekzistuese nuk përputhen me rregullat e reja")
            if json.loads(linked["rules_snapshot"])["platforms"] != rules.platforms:
                raise HTTPException(409, "Platformat kanë ndryshuar; ruaj kufizimet origjinale për këtë punë")
            if linked["revision"] != linked["latest"]:
                db.execute(
                    "UPDATE job_campaigns SET revision=?,rules_snapshot=? WHERE job_id=?",
                    (linked["latest"], linked["data"], job_id),
                )
                db.execute(
                    "DELETE FROM reviews WHERE passed=1 AND clip_id IN (SELECT id FROM clips WHERE job_id=?)",
                    (job_id,),
                )
                # Recheck the existing artifact; never replace media already referenced by an upload session.
                db.execute(
                    """UPDATE clips SET status='checking' WHERE job_id=? AND status IN ('ready','approved')
                    AND EXISTS (SELECT 1 FROM jobs WHERE id=? AND review_mode='automatic')""",
                    (job_id, job_id),
                )
                db.execute(
                    """UPDATE jobs SET status='processing',stage='render',completed=NULL WHERE id=?
                    AND review_mode='automatic' AND EXISTS (SELECT 1 FROM clips WHERE job_id=? AND status='checking')""",
                    (job_id, job_id),
                )
        return {"ok": True}

    @router.put("/submissions/{publication_id}")
    def record(publication_id: str, body: Submission):
        with store.connect(immediate=True) as db:
            publication = db.execute(
                """SELECT p.* FROM publications p JOIN clips c ON c.id=p.clip_id
                JOIN job_campaigns jc ON jc.job_id=c.job_id WHERE p.id=?""",
                (publication_id,),
            ).fetchone()
            if not publication or publication["status"] != "published":
                raise HTTPException(409, "Duhet një postim i publikuar nga një fushatë")
            if body.status != "ready" and not publication["url"]:
                raise HTTPException(422, "Regjistro linkun publik te Klipet para dorëzimit")
            if body.status != "ready" and not body.reference.strip():
                raise HTTPException(422, "Shto referencën ose shënimin e konfirmimit nga clipping.net")
            old = db.execute("SELECT * FROM submissions WHERE publication_id=?", (publication_id,)).fetchone()
            data = body.model_dump()
            if old and all(old[key] == value for key, value in data.items()):
                return {"ok": True}
            db.execute(
                """INSERT INTO submissions VALUES (?,?,?,?,?,?) ON CONFLICT(publication_id) DO UPDATE SET
                status=excluded.status,bounty=excluded.bounty,reference=excluded.reference,notes=excluded.notes,updated=excluded.updated""",
                (publication_id, body.status, body.bounty, body.reference, body.notes, time.time()),
            )
            db.execute(
                "INSERT INTO submission_history(publication_id,data,created) VALUES (?,?,?)",
                (publication_id, encode(data), time.time()),
            )
        return {"ok": True}

    return router

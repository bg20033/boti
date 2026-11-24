"""Versioned title edits re-enter rendering and quality review."""

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from boti.db import encode
from boti.editing import clean_text


class TitleEdit(BaseModel):
    text: str = Field(min_length=1, max_length=120)
    expected_version: int = Field(ge=1)

    @field_validator("text")
    @classmethod
    def plain_text(cls, value):
        value = clean_text(value)
        if not value:
            raise ValueError("Shkruaj titullin")
        return value


def routes(store, admin):
    router = APIRouter(prefix="/api/clips", dependencies=[Depends(admin)])

    @router.post("/{clip_id}/title")
    def edit_title(clip_id: str, body: TitleEdit):
        with store.connect(immediate=True) as db:
            clip = db.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
            if not clip:
                raise HTTPException(404, "Klipi nuk gjendet")
            if clip["current_version"] != body.expected_version:
                raise HTTPException(409, "Klipi ndryshoi ndërkohë; rifresko bibliotekën")
            if db.execute("SELECT 1 FROM publications WHERE clip_id=?", (clip_id,)).fetchone():
                raise HTTPException(409, "Klipi ka hyrë në publikim; krijo një provë të re për editim")
            job = db.execute("SELECT * FROM jobs WHERE id=?", (clip["job_id"],)).fetchone()
            if job["review_mode"] != "automatic":
                raise HTTPException(409, "Rieditimi kërkon punë me kontroll automatik")
            source = json.loads(job["metadata"]).get("source")
            if not source or not store.config.path(source).is_file():
                raise HTTPException(409, "Burimi origjinal mungon; shtoje videon si provë të re")
            metadata = json.loads(clip["metadata"])
            if metadata.get("top_title_override") == body.text:
                return {"version": clip["current_version"], "changed": False}
            version, now = clip["current_version"] + 1, time.time()
            db.execute(
                "INSERT OR IGNORE INTO clip_versions(clip_id,version,metadata,asset_id,created) VALUES (?,?,?,?,?)",
                (clip_id, clip["current_version"], clip["metadata"], clip["asset_id"], now),
            )
            review = db.execute(
                "SELECT passed,notes,updated FROM reviews WHERE clip_id=?", (clip_id,)
            ).fetchone()
            if review:
                db.execute(
                    "INSERT INTO events(level,message,job_id,created) VALUES ('info',?,?,?)",
                    (
                        f"Vlerësimi para rieditimit të {clip_id} v{clip['current_version']}: "
                        + encode(dict(review)),
                        job["id"],
                        now,
                    ),
                )
            metadata["top_title_override"] = body.text
            db.execute(
                """UPDATE clips SET current_version=?,metadata=?,asset_id=NULL,status='preparing',
                quality_reason=NULL,updated=? WHERE id=?""",
                (version, encode(metadata), now, clip_id),
            )
            # A prior human approval belongs to the old bytes/title.
            db.execute("DELETE FROM reviews WHERE clip_id=?", (clip_id,))
            db.execute(
                "UPDATE jobs SET status='processing',stage='render',completed=NULL,updated=? WHERE id=?",
                (now, job["id"]),
            )
        store.event("Titulli u ndryshua; klipi po rieditohet dhe rikontrollohet", job_id=job["id"])
        return {"version": version, "changed": True}

    return router

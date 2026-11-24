from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import BaseModel, Field

from boti.styles import EditStyle


def private_write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(content.encode() if isinstance(content, str) else content)
    if os.name != "nt":
        path.chmod(0o600)


class Settings(BaseModel):
    min_free_gb: float = Field(default=20, ge=0)
    chunk_seconds: int = Field(default=300, ge=120, le=600)
    overlap_seconds: int = Field(default=60, ge=60, le=90)
    score_threshold: float = Field(default=75, ge=0, le=100)
    lease_seconds: int = Field(default=120, ge=30, le=600)
    paused: bool = False
    publishing_enabled: bool = False
    posts_per_account_daily: int = Field(default=3, ge=1, le=1000)
    post_interval_minutes: int = Field(default=60, ge=0, le=1440)
    youtube_daily_uploads: int = Field(default=100, ge=1, le=100000)
    instagram_api_version: str = "v24.0"
    original_retention_days: int = Field(default=7, ge=1)
    trial_source_retention_hours: int = Field(default=24, ge=1, le=168)
    output_retention_days: int = Field(default=30, ge=1)
    worker_archive_enabled: bool = True
    worker_cache_minutes: int = Field(default=10, ge=1, le=1440)
    target_clips_daily: int = Field(default=1000, ge=1)
    max_active_jobs: int = Field(default=2, ge=1, le=20)
    max_prepared_tasks: int = Field(default=8, ge=1, le=100)
    download_fragments: int = Field(default=4, ge=1, le=16)
    caption_style: str = Field(default="highlight", pattern="^(highlight|plain)$")
    analysis_worker_id: str | None = Field(default=None, min_length=1, max_length=100)
    quality_worker_id: str | None = Field(default=None, min_length=1, max_length=100)
    editing: EditStyle = Field(default_factory=EditStyle)


class Config:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("BOTI_DATA", "data")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("media", "exports", "secrets", "inbox"):
            (self.root / name).mkdir(exist_ok=True)
        key_path = self.root / "secrets" / "vault.key"
        if not key_path.exists():
            private_write(key_path, Fernet.generate_key())
        self.vault = Fernet(key_path.read_bytes())
        session = self.root / "secrets" / "session.key"
        if not session.exists():
            private_write(session, secrets.token_urlsafe(48))
        self.session_key = session.read_text().strip()
        self.db_path = self.root / "boti.sqlite3"

    def encrypt(self, value: dict) -> str:
        return self.vault.encrypt(json.dumps(value).encode()).decode()

    def decrypt(self, value: str) -> dict:
        return json.loads(self.vault.decrypt(value.encode()))

    def path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root / "media") and not path.is_relative_to(self.root / "exports"):
            raise ValueError("Path outside media storage")
        return path

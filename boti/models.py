from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from boti.sources import vod_url


def twitch_url(value: str) -> tuple[str, str]:
    url, provider, video_id = vod_url(value)
    if provider != "twitch":
        raise ValueError("Expected a Twitch VOD")
    return url, video_id


class TrialOptions(BaseModel):
    start_seconds: int = Field(default=0, ge=0, le=604800)
    duration_seconds: int = Field(default=300, ge=30, le=900)
    max_clips: int = Field(default=5, ge=3, le=5)


class JobCreate(BaseModel):
    trial: TrialOptions | None = None
    url: str = Field(default="", max_length=1000)
    file: str | None = Field(default=None, max_length=255)
    source_label: str = Field(default="", max_length=60)
    provider: Literal["twitch", "kick", "youtube", "file"] | None = None
    review_mode: Literal["automatic", "manual"] = "automatic"
    campaign_id: str | None = Field(default=None, max_length=100)
    group_ids: list[str] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def canonicalize(self):
        if bool(self.url) == bool(self.file):
            raise ValueError("Jep një link videoje ose një skedar nga data/inbox, jo të dyja")
        if self.file:
            if self.provider not in (None, "file"):
                raise ValueError("Platforma nuk përputhet me skedarin")
            self.provider = "file"
            self.source_label = re.sub(r"[^\w .-]", "", self.source_label).strip()
            return self
        self.url, provider, _ = vod_url(self.url)
        if self.provider and self.provider != provider:
            raise ValueError("Platforma nuk përputhet me linkun")
        self.provider = provider
        return self


class Box(BaseModel):
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def bounds(self):
        if self.x + self.w > 1.00001 or self.y + self.h > 1.00001:
            raise ValueError("Camera box must fit inside the image")
        return self


class Candidate(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    score: float = Field(ge=0, le=100)
    title: str = Field(min_length=1, max_length=100)
    caption: str = Field(default="", max_length=1800)
    hashtags: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(default="", max_length=1000)
    verification_reason: str = Field(default="", max_length=1000)
    content_type: str = Field(default="mixed", pattern="^(talk|gaming|mixed)$")

    @model_validator(mode="after")
    def duration(self):
        # Absolute engine bounds. The campaign's own range is enforced with `within`.
        if not 5 <= self.end - self.start <= 180.05:
            raise ValueError("Clips must be between 5 and 180 seconds")
        cleaned = [re.sub(r"[^\w]", "", tag).strip("_")[:30] for tag in self.hashtags]
        self.hashtags = list(dict.fromkeys(tag for tag in cleaned if tag))
        return self


def within(candidate: dict, limits: dict | None) -> bool:
    """Campaign duration range; jobs without a campaign keep the original 20–60 seconds."""
    limits = limits or {}
    length = candidate["end"] - candidate["start"]
    return limits.get("min_seconds", 20) <= length <= limits.get("max_seconds", 60) + 0.05


class AnalysisResult(BaseModel):
    candidates: list[Candidate] = Field(max_length=16)


class Word(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    word: str = Field(max_length=200)


class Segment(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(max_length=10000)
    words: list[Word] = Field(default_factory=list, max_length=2000)


class Transcript(BaseModel):
    language: str = Field(default="unknown", max_length=30)
    segments: list[Segment] = Field(max_length=2000)


class LeaseRequest(BaseModel):
    lease_token: str


class CompleteRequest(LeaseRequest):
    result: dict


class FailureRequest(LeaseRequest):
    error: str = Field(max_length=2000)
    retryable: bool = True


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    preferred_worker: str | None = None


class GroupUpdate(BaseModel):
    paused: bool


class SourceProfile(BaseModel):
    mode: str = Field(default="auto", pattern="^(auto|talk|gaming)$")
    camera: Box | None = None
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")
    vocabulary: str = Field(default="", max_length=500)

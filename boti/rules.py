"""Structured campaign clip rules: one source of truth for cutting, editing, captions and compliance.

The operator copies the campaign's real rules from clipping.net into the panel. Nothing here contacts
clipping.net, and passing these checks never establishes permission or payout eligibility.
"""

from __future__ import annotations

import json
import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

Position = Literal["top_left", "top", "top_right", "bottom_left", "bottom", "bottom_right"]
PLATFORM_CAPTION_LIMIT = {"youtube": 5000, "instagram": 2200, "tiktok": 2200}
LANGUAGES = {
    "english": "en",
    "albanian": "sq",
    "german": "de",
    "french": "fr",
    "spanish": "es",
    "italian": "it",
    "portuguese": "pt",
    "turkish": "tr",
    "arabic": "ar",
    "hindi": "hi",
}


def _tag(value: str) -> str:
    return re.sub(r"[^\w]", "", str(value)).strip("_")[:50]


def _handle(value: str) -> str:
    return re.sub(r"[^\w.]", "", str(value)).strip("._")[:50]


def _unique(values):
    seen, result = set(), []
    for value in values:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


class ClipRules(BaseModel):
    """What a campaign demands of every clip. Defaults reproduce the pre-0.5 behaviour."""

    min_seconds: int = Field(default=20, ge=5, le=170)
    max_seconds: int = Field(default=60, ge=10, le=180)
    captions: bool = True
    hook_title: bool = False
    watermark_text: str = Field(default="", max_length=40)
    watermark_position: Position = "top_right"
    logo_sha256: str = Field(default="", pattern=r"^([0-9a-f]{64})?$")
    logo_position: Position = "top_left"
    required_hashtags: list[str] = Field(default_factory=list, max_length=10)
    required_mentions: list[str] = Field(default_factory=list, max_length=5)
    caption_suffix: str = Field(default="", max_length=300)
    banned_words: list[str] = Field(default_factory=list, max_length=60)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")
    allowed_sources: list[str] = Field(default_factory=list, max_length=50)
    max_clips_per_video: int | None = Field(default=None, ge=1, le=200)
    max_posts_per_account_daily: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def normalise(self):
        if self.max_seconds - self.min_seconds < 5:
            raise ValueError("Kohëzgjatja maksimale duhet të jetë së paku 5 sekonda mbi minimumin")
        self.watermark_text = " ".join(re.sub(r"[{}\\\x00-\x1f\x7f]", " ", self.watermark_text).split())
        self.caption_suffix = " ".join(self.caption_suffix.split())
        self.required_hashtags = _unique(_tag(v) for v in self.required_hashtags)
        self.required_mentions = _unique(_handle(v) for v in self.required_mentions)
        self.banned_words = _unique(" ".join(str(v).casefold().split())[:60] for v in self.banned_words)
        self.allowed_sources = _unique(" ".join(str(v).split())[:200] for v in self.allowed_sources)
        return self


def clip_rules(campaign: dict | str | None) -> ClipRules:
    """Clip rules from a stored campaign document or job snapshot; older documents get the defaults."""
    if isinstance(campaign, str):
        campaign = json.loads(campaign) if campaign else {}
    return ClipRules.model_validate((campaign or {}).get("clip") or {})


def chunking(settings, rules: ClipRules) -> tuple[int, int]:
    """Analysis windows must overlap by a whole clip, or a moment on a window edge is never seen whole."""
    overlap = max(settings.overlap_seconds, rules.max_seconds)
    chunk = max(settings.chunk_seconds, min(600, rules.max_seconds * 3), overlap + 120)
    return chunk, overlap


def analysis_limits(campaign: dict | None) -> dict:
    rules = clip_rules(campaign)
    return {
        "min_seconds": rules.min_seconds,
        "max_seconds": rules.max_seconds,
        "banned_words": rules.banned_words,
        "language": rules.language,
        "brief": str((campaign or {}).get("rules", ""))[:3000],
    }


def render_rules(campaign: dict | None) -> dict:
    rules = clip_rules(campaign)
    return rules.model_dump(
        include={
            "min_seconds",
            "max_seconds",
            "captions",
            "hook_title",
            "watermark_text",
            "watermark_position",
            "logo_sha256",
            "logo_position",
        }
    )


def banned_hits(text: str, words: list[str]) -> list[str]:
    folded = " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())
    return [w for w in words if w and re.search(rf"(?<!\w){re.escape(w)}(?!\w)", folded)]


def spoken_text(meta: dict) -> str:
    return " ".join(str(s.get("text", "")) for s in meta.get("segments", []))


def final_hashtags(meta: dict, rules: ClipRules | None = None) -> list[str]:
    required = rules.required_hashtags if rules else []
    own = [_tag(tag) for tag in meta.get("hashtags", [])]
    return _unique([*required, *own])[:12]


def final_caption(meta: dict, rules: ClipRules | None = None, platform: str | None = None) -> str:
    """Post text with every campaign-required tag, mention and disclosure. Required parts are never cut."""
    tags = " ".join("#" + tag for tag in final_hashtags(meta, rules))
    mentions = " ".join("@" + m for m in (rules.required_mentions if rules else []))
    suffix = rules.caption_suffix if rules else ""
    required = "\n\n".join(part for part in (mentions, suffix, tags) if part)
    body = (meta.get("caption") or meta.get("title") or "").strip()
    limit = PLATFORM_CAPTION_LIMIT.get(platform or "", 2200)
    room = limit - len(required) - (2 if required else 0)
    if len(body) > room:
        body = body[: max(0, room - 1)].rstrip() + "…" if room > 1 else ""
    return "\n\n".join(part for part in (body, required) if part)


def _source_keys(value: str, *, restriction=False) -> set[tuple]:
    """URLs are scoped to their platform; VOD restrictions authorize only that exact VOD."""
    from boti.sources import vod_url

    value = value.strip()
    if "://" not in value:
        return {("name", value.lstrip("@").casefold())} if value else set()
    try:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
            return set()
        host = (parsed.hostname or "").removeprefix("www.")
        if host == "m.youtube.com":
            host = "youtube.com"
        platform = {
            "kick.com": "kick",
            "twitch.tv": "twitch",
            "youtube.com": "youtube",
            "youtu.be": "youtube",
        }.get(host)
        if not platform:
            return set()
        keys = set()
        try:
            _, provider, identity = vod_url(value)
            keys.add(("video", provider, identity))
            if restriction:
                return keys
        except ValueError:
            pass
        parts = parsed.path.strip("/").split("/")
        channel = None
        if (
            platform in {"kick", "twitch"}
            and (len(parts) == 1 or (platform == "kick" and len(parts) == 3 and parts[1] == "videos"))
            and re.fullmatch(r"[\w-]+", parts[0])
            and parts[0] not in {"videos", "directory", "settings"}
        ):
            channel = parts[0].casefold()
        elif host == "youtube.com":
            if len(parts) == 1 and re.fullmatch(r"@[\w.-]+", parts[0]):
                channel = parts[0].casefold()
            elif len(parts) == 2 and parts[0] == "channel" and re.fullmatch(r"UC[\w-]+", parts[1]):
                channel = parts[1]  # YouTube channel IDs are case-sensitive.
        if channel:
            keys.add(("channel", platform, channel))
            if not restriction:
                keys.add(("name", channel.lstrip("@").casefold()))
        return keys
    except ValueError:
        return set()


def source_allowed(rules: ClipRules, *identities) -> bool:
    if not rules.allowed_sources:
        return True
    actual = set().union(*(_source_keys(str(value)) for value in identities if value))
    return any(_source_keys(allowed, restriction=True) & actual for allowed in rules.allowed_sources)


def job_source_allowed(rules: ClipRules, job: dict) -> bool:
    metadata = job.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    channel = metadata.get("channel", "")
    provider = job.get("provider", "twitch")
    name = channel.removeprefix(provider + ":")
    identities = [job.get("url"), name, *metadata.get("source_identities", [])]
    if provider == "file":
        identities.extend([metadata.get("source_label"), (job.get("url") or "").removeprefix("file:")])
    elif provider == "youtube" and re.fullmatch(r"UC[\w-]+", name):
        identities.append("https://youtube.com/channel/" + name)
    elif provider == "twitch" and re.fullmatch(r"[\w-]+", name):
        identities.append("https://twitch.tv/" + name)
    return source_allowed(rules, *identities)


def compliance(meta: dict, rules: ClipRules, length: float, render: dict | None = None) -> dict:
    """Deterministic rule checks. `fixable` issues disappear when the clip is rendered again."""
    issues = []

    def issue(code, message, fixable=False):
        issues.append({"code": code, "message": message, "fixable": fixable})

    if not rules.min_seconds - 0.25 <= length <= rules.max_seconds + 0.5:
        issue(
            "duration",
            f"Kohëzgjatja {length:.1f}s është jashtë kufirit {rules.min_seconds}–{rules.max_seconds}s",
        )
    hits = banned_hits(
        " ".join((spoken_text(meta), meta.get("title", ""), meta.get("caption", ""))), rules.banned_words
    )
    if hits:
        issue("banned_words", "Fjalë të ndaluara nga fushata: " + ", ".join(hits[:8]))
    text = final_caption(meta, rules).casefold()
    missing = [
        *("#" + t for t in rules.required_hashtags if "#" + t.casefold() not in text),
        *("@" + m for m in rules.required_mentions if "@" + m.casefold() not in text),
    ]
    if missing or (rules.caption_suffix and rules.caption_suffix.casefold() not in text):
        issue("caption", "Përshkrimit i mungojnë elementet e kërkuara: " + ", ".join(missing or ["teksti"]))
    if render is not None:
        overlays = render.get("overlays") or {}
        if rules.watermark_text and overlays.get("watermark") != rules.watermark_text:
            issue("watermark", "Videoja nuk ka watermark-un aktual të fushatës", True)
        if rules.logo_sha256 and overlays.get("logo") != rules.logo_sha256:
            issue("logo", "Videoja nuk ka logon aktuale të fushatës", True)
        if rules.captions and overlays.get("captions") is False and spoken_text(meta).strip():
            issue("captions", "Fushata kërkon titra, por videoja u editua pa to", True)
        if not rules.captions and overlays.get("captions"):
            issue("captions", "Fushata nuk lejon titra të djegur në video", True)
    return {"passed": not issues, "issues": issues}


def _count(value: str, unit: str | None) -> int:
    number = float(value.replace(",", ""))
    return int(number * {"k": 1_000, "m": 1_000_000}.get((unit or "").lower(), 1))


def _seconds(value: str, unit: str) -> int:
    return round(float(value) * (60 if unit.lower().startswith("m") else 1))


_TIME = r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m)\b(?!\s*views)"


def parse_rules_text(text: str) -> dict:
    """Suggest structured fields from pasted campaign rules. Heuristic: the operator confirms every value."""
    clip, campaign, notes = {}, {}, []
    flat = " ".join(text.split())
    lower = flat.lower()

    span = re.search(rf"(\d+(?:\.\d+)?)\s*(?:-|–|—|to|and)\s*{_TIME}", lower)
    if span:
        clip["min_seconds"] = _seconds(span[1], span[3])
        clip["max_seconds"] = _seconds(span[2], span[3])
    low = re.search(
        rf"(?:at least|min(?:imum)?(?: of| length| duration)?:?|longer than|over)\s*{_TIME}", lower
    )
    if low and "min_seconds" not in clip:
        clip["min_seconds"] = _seconds(low[1], low[2])
    high = re.search(
        rf"(?:at most|max(?:imum)?(?: of| length| duration)?:?|no longer than|shorter than|under|up to|less than)\s*{_TIME}",
        lower,
    )
    if high and "max_seconds" not in clip:
        clip["max_seconds"] = _seconds(high[1], high[2])
    for key, floor, ceiling in (("min_seconds", 5, 170), ("max_seconds", 10, 180)):
        if key in clip and not floor <= clip[key] <= ceiling:
            notes.append(
                f"Kohëzgjatja {clip.pop(key)}s është jashtë kufijve që mbështet Boti ({floor}–{ceiling}s)"
            )

    tags = _unique(_tag(t) for t in re.findall(r"(?<!\w)#(\w{2,50})", flat) if not t.isdigit())
    if tags:
        clip["required_hashtags"] = tags[:10]
    mentions = _unique(_handle(m) for m in re.findall(r"(?<![\w.])@(\w[\w.]{1,49})", flat))
    if mentions:
        clip["required_mentions"] = mentions[:5]

    if re.search(
        r"\b(no|without|don'?t (?:add|use)|do not (?:add|use))\s+(?:burned[- ]in\s+)?(captions|subtitles)",
        lower,
    ):
        clip["captions"] = False
    elif re.search(
        r"(captions|subtitles)\s+(?:are\s+)?(required|mandatory|must)|must (?:have|include|add)\s+(captions|subtitles)",
        lower,
    ):
        clip["captions"] = True
    for name, code in LANGUAGES.items():
        if re.search(rf"\b{name}\b(?:[- ]language)?\s+(?:only|clips|content|audio)|\bin {name}\b", lower):
            clip["language"] = code
            break
    if re.search(r"\b(watermark|logo|overlay)\b", lower):
        notes.append("Fushata përmend watermark/logo: shkruaj tekstin ose ngarko logon e dhënë nga fushata")
    if re.search(r"\b(sound|audio track|song)\b", lower):
        notes.append("Fushata përmend tingull/muzikë: tingulli i platformës shtohet me dorë gjatë postimit")

    views = re.search(r"min(?:imum)?(?: of)?\s*(\d[\d,.]*)\s*([km])?\s*views", lower)
    if views:
        campaign["min_post_views"] = _count(views[1], views[2])
    rate = re.search(
        r"(?:\$|usd\s*)(\d+(?:\.\d+)?)\s*(?:per|/|for every|for)\s*(1k|1,000|1000|100k|100,000|100000|1m|1,000,000|million)\s*views",
        lower,
    )
    if rate:
        unit = rate[2].replace(",", "")
        if unit in {"1k", "1000"}:
            campaign.update(rate=rate[1], rate_unit=1000)
        elif unit in {"100k", "100000"}:
            campaign.update(rate=rate[1], rate_unit=100000)
        else:
            campaign.update(rate=f"{float(rate[1]) / 10:.6f}".rstrip("0").rstrip("."), rate_unit=100000)
    accounts = re.search(
        r"(?:max(?:imum)?(?: of)?|up to|limit(?:ed)? to)?\s*(\d+)\s*accounts?\s*(?:per|/|on each)\s*platform",
        lower,
    )
    if accounts:
        campaign["max_accounts_per_platform"] = int(accounts[1])
    found = [p for p in ("tiktok", "instagram", "youtube") if p in lower]
    if found and len(found) < 3:
        campaign["platforms"] = found
    bans = [
        line.strip(" -•*\t")[:160]
        for line in text.splitlines()
        if re.match(r"\s*[-•*]?\s*(no|do not|don'?t|never|avoid)\b", line, re.I)
    ]
    if bans:
        notes.append("Ndalime për t'i kontrolluar vetë: " + " | ".join(bans[:8]))
    return {"clip": clip, "campaign": campaign, "notes": notes}

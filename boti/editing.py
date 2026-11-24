"""Deterministic editing rules shared by selection, captions and quality checks."""

from __future__ import annotations

import math
import re
import unicodedata

SENTENCE_END = re.compile(r"[.!?…。！？][\"\'”’»)]*$")
TRAILING_OFF = re.compile(r"(?:\.{2,}|…+)[\"\'”’»)]*$")


def sentence_finished(text: str) -> bool:
    text = text.strip()
    return bool(SENTENCE_END.search(text) and not TRAILING_OFF.search(text))


def boundary_evidence(segments: list[dict], start: float, end: float) -> dict:
    """Observable cut defects, not a claim that punctuation proves a complete story."""
    words = sorted(
        [w for s in segments for w in s.get("words", []) if w["end"] > w["start"]],
        key=lambda w: w["start"],
    )
    active = [w for w in words if w["end"] > start and w["start"] < end]
    issues = []
    if any(w["start"] + 0.03 < start < w["end"] - 0.03 for w in words):
        issues.append("Prerja e fillimit kalon brenda një fjale")
    if any(w["start"] + 0.03 < end < w["end"] - 0.03 for w in words):
        issues.append("Prerja e fundit kalon brenda një fjale")
    if active and TRAILING_OFF.search(active[-1]["word"].strip()):
        issues.append("Fjalia në fund mbetet e papërfunduar (…)")
    previous = next((w for w in reversed(words) if w["end"] <= start), None)
    following = next((w for w in words if w["start"] >= end), None)
    if (
        active
        and previous
        and not sentence_finished(previous["word"])
        and active[0]["start"] - previous["end"] < 0.5
    ):
        issues.append("Prerja e fillimit nis në mes të një fjalie të vazhdueshme")
    if (
        active
        and following
        and not sentence_finished(active[-1]["word"])
        and following["start"] - active[-1]["end"] < 0.5
    ):
        issues.append("Prerja e fundit e ndërpret fjalinë para vazhdimit")
    return {
        "passed": not issues,
        "issues": issues,
        "first_words": " ".join(w["word"].strip() for w in active[:8]),
        "last_words": " ".join(w["word"].strip() for w in active[-8:]),
        "speech_starts_after": round(max(0, active[0]["start"] - start), 3) if active else None,
        "silence_at_end": round(max(0, end - active[-1]["end"]), 3) if active else None,
    }


def clean_text(text: str) -> str:
    # Only our renderer may emit ASS commands. Source text is always plain text.
    return " ".join(re.sub(r"[{}\\\x00-\x1f\x7f]", " ", text).split())


def text_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1 for c in text)


def caption_phrases(segments: list[dict], length: float) -> list[list[dict]]:
    """Readable phrases with real word timing when available; never bridge a long pause."""
    timeline = []
    for segment in segments:
        words = segment.get("words") or []
        if words:
            timeline.extend({**word, "timed": True} for word in words)
        else:
            tokens = clean_text(segment["text"]).split()
            total = sum(len(token) for token in tokens)
            cursor = segment["start"]
            for token in tokens:
                end = cursor + (segment["end"] - segment["start"]) * len(token) / max(1, total)
                timeline.append({"start": cursor, "end": end, "word": token, "timed": False})
                cursor = end
    phrases, group = [], []
    previous_end = 0.0
    for word in sorted(timeline, key=lambda item: (item["start"], item["end"])):
        start, end = max(0, previous_end, word["start"]), min(length, word["end"])
        text = clean_text(word["word"])
        if not text or not all(math.isfinite(t) for t in (start, end)) or end <= start:
            continue
        item = {**word, "start": start, "end": end, "word": text}
        if group and (
            len(group) >= 6
            or start - group[-1]["end"] > 0.45
            or end - group[0]["start"] > 2.8
            or SENTENCE_END.search(group[-1]["word"])
            or len(caption_lines([*group, item])) > 2
        ):
            phrases.append(group)
            group = []
        group.append(item)
        previous_end = end
    if group:
        phrases.append(group)
    return phrases


def caption_lines(words: list[dict]) -> list[list[int]]:
    lines: list[list[int]] = [[]]
    width = 0
    for index, word in enumerate(words):
        size = text_width(word["word"])
        if lines[-1] and width + 1 + size > 26:
            lines.append([])
            width = 0
        lines[-1].append(index)
        width += size + (1 if len(lines[-1]) > 1 else 0)
    return lines


def wrap_title(text: str, width=22, rows=3) -> list[str]:
    """Short on-screen hook lines. Overflow is dropped at a word boundary, never squeezed."""
    lines: list[str] = []
    for word in clean_text(text).split():
        if lines and text_width(lines[-1]) + 1 + text_width(word) <= width:
            lines[-1] += " " + word
        elif len(lines) < rows:
            lines.append(word)
        else:
            lines[-1] = lines[-1].rstrip(".,;:") + "…"
            break
    return lines


def refine_boundaries(
    candidate: dict, segments: list[dict], length: float, min_length: float = 20, max_length: float = 60
) -> dict | None:
    """Snap nearby speech cuts to sentences/pauses, without moving a visual-only event."""
    start, end = candidate["start"], candidate["end"]
    if end > length + 0.1:
        return None
    words = sorted(
        [word for segment in segments for word in segment.get("words", []) if word["end"] > word["start"]],
        key=lambda word: word["start"],
    )
    starts, ends = [], []
    if words:
        for index, word in enumerate(words):
            previous = words[index - 1] if index else None
            following = words[index + 1] if index + 1 < len(words) else None
            if not previous or sentence_finished(previous["word"]) or word["start"] - previous["end"] >= 0.5:
                starts.append(max(0, word["start"] - 0.08, previous["end"] if previous else 0))
            if not TRAILING_OFF.search(word["word"].strip()) and (
                not following or sentence_finished(word["word"]) or following["start"] - word["end"] >= 0.5
            ):
                ends.append(min(length, word["end"] + 0.12, following["start"] if following else length))
    else:
        starts = [max(0, s["start"] - 0.08) for s in segments]
        ends = [min(length, s["end"] + 0.12) for s in segments]
    # If sentence alignment isn't possible, at least avoid cutting through a spoken word.
    start_inside = next((w for w in words if w["start"] < start < w["end"]), None)
    end_inside = next((w for w in words if w["start"] < end < w["end"]), None)
    near_starts = [s for s in starts if abs(s - start) <= 2]
    # A sentence/payoff can continue farther than two seconds. Never pad past the recording.
    near_ends = [e for e in ends if abs(e - end) <= 6]
    near_starts.append(max(0, start_inside["start"] - 0.08) if start_inside else start)
    near_ends.append(min(length, end_inside["end"] + 0.12) if end_inside else min(end, length))
    choices = [
        (s, e)
        for s in near_starts
        for e in near_ends
        if min_length <= e - s <= max_length
        and not any(w["start"] < s < w["end"] or w["start"] < e < w["end"] for w in words)
        and boundary_evidence(segments, s, e)["passed"]
    ]
    if not choices:
        return None
    # Prefer nearby complete sentences, then the least movement of the original cut.
    s, e = min(
        choices,
        key=lambda pair: (
            int(pair[0] not in starts) + int(pair[1] not in ends),
            abs(pair[0] - start) + abs(pair[1] - end),
        ),
    )
    return {**candidate, "start": round(s, 4), "end": round(e, 4)}

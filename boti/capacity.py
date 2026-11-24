"""Capacity scenarios from observed task time, not promises about content quality."""

import math
import time


def estimate(store):
    since = time.time() - 86400
    settings = store.settings()
    main = store.one(
        """SELECT count(*) samples,sum(finished-started) seconds FROM tasks
        WHERE status='complete' AND kind IN ('analyze','quality') AND worker_id=?
        AND finished>? AND started IS NOT NULL""",
        (settings.analysis_worker_id or "main-local", since),
    )
    clips = store.one(
        """SELECT count(*) n FROM clips c WHERE created>? AND EXISTS
        (SELECT 1 FROM quality_reports q WHERE q.clip_id=c.id AND q.version=c.current_version)""",
        (since,),
    )["n"]
    # Deleted clips remain historical samples, never active library items.
    seconds = (main["seconds"] / clips) if clips and main["seconds"] else None
    low = math.floor(86400 / seconds * 0.5) if seconds else None
    high = math.floor(86400 / seconds * 0.75) if seconds else None
    scenarios = (
        [
            {
                "acceptance_percent": p,
                "ready_low": math.floor(low * p / 100),
                "ready_high": math.floor(high * p / 100),
            }
            for p in (25, 50, 75)
        ]
        if seconds
        else []
    )
    connected = store.all(
        "SELECT platform,count(*) n FROM accounts a JOIN groups g ON g.id=a.group_id WHERE a.status='connected' AND g.paused=0 GROUP BY platform"
    )
    return {
        "basis": "historical_main_ai_time_24h",
        "sample_tasks": main["samples"],
        "sample_clips": clips,
        "main_seconds_per_clip": round(seconds, 1) if seconds else None,
        "processed_low": low,
        "processed_high": high,
        "quality_scenarios": scenarios,
        "assumptions": "24h online, sources and disk available; 50–75% main availability. Updated caption checks need a fresh baseline.",
        "operator_posts_per_account_daily": settings.posts_per_account_daily,
        "operator_interval_minutes": settings.post_interval_minutes,
        "connected_accounts": connected,
        "publishing_enabled": settings.publishing_enabled,
        "current_automatic_posts": 0 if not settings.publishing_enabled else None,
        "youtube_project_configured_daily": settings.youtube_daily_uploads,
    }

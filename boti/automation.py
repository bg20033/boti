"""Read-only readiness for the complete feed-to-post workflow; never enables publishing."""

import json
import time

from boti.feeds import FeedConfig, validate_destination

PLATFORMS = ("youtube", "instagram", "tiktok", "x")


def automation_status(store, snapshot):
    settings = snapshot["settings"]
    feeds = [row for row in snapshot["feeds"] if row["data"]["enabled"]]
    full = [row for row in feeds if not row["data"].get("trial")]
    selected = {group for row in feeds for group in row["data"]["group_ids"]}
    groups = {row["id"]: row for row in snapshot["groups"]}
    accounts = store.all(
        "SELECT group_id,platform,status,credentials IS NOT NULL AND credentials!='' linked FROM accounts"
    )
    platforms = []
    for platform in PLATFORMS:
        connected = {
            row["group_id"]
            for row in accounts
            if row["platform"] == platform and row["status"] == "connected" and row["linked"]
        }
        missing = sorted(selected - connected)
        platforms.append(
            {
                "platform": platform,
                "connected_groups": len(selected & connected),
                "required_groups": len(selected),
                "missing_groups": missing,
                "ready": bool(selected) and not missing,
            }
        )

    campaign_issues = []
    for row in full:
        config = FeedConfig.model_validate(row["data"])
        issues = []
        try:
            validate_destination(store, config)
        except ValueError as exc:
            issues.append(str(exc))
        if config.campaign_id:
            campaign = store.one("SELECT data FROM campaigns WHERE id=?", (config.campaign_id,))
            if campaign:
                data = json.loads(campaign["data"])
                if data["status"] != "active":
                    issues.append("Aktivizo fushatën")
                if not data["rules_confirmed"] or not data["automation_confirmed"]:
                    issues.append("Konfirmo rregullat dhe lejimin e automatizimit të fushatës")
        if any(groups.get(group, {}).get("paused", True) for group in config.group_ids):
            issues.append("Grupi destinacion është pezulluar ose mungon")
        if issues:
            campaign_issues.append({"channel": config.channel, "issues": list(dict.fromkeys(issues))})

    processing_ready = (
        not settings["paused"] and not snapshot["disk"]["paused"] and snapshot["readiness"]["workers_ready"]
    )
    sources_ready = bool(feeds) and len(full) == len(feeds) and not campaign_issues
    accounts_ready = all(row["ready"] for row in platforms)
    checks = [
        {
            "id": "processing",
            "ready": processing_ready,
            "label": "Gjetja, editimi dhe kontrolli",
            "detail": "PC-të dhe disku janë gati"
            if processing_ready
            else "Kontrollo PC-të, pauzën dhe hapësirën në disk",
            "view": "dashboard",
        },
        {
            "id": "feeds",
            "ready": sources_ready,
            "label": "Burimet për publikim",
            "detail": f"{len(full)}/{len(feeds)} kanale aktive me VOD të plotë; {len(feeds) - len(full)} në Provë. "
            + (
                "Gati për përpunim normal; fushata është opsionale."
                if sources_ready
                else "Aktivizo përpunimin normal; kontrollo rregullat nëse ke lidhur fushatë."
            ),
            "view": "settings",
            "section": "automatic-feeds",
        },
        {
            "id": "accounts",
            "ready": accounts_ready,
            "label": "TikTok · Instagram · YouTube · X",
            "detail": f"{sum(row['ready'] for row in platforms)}/4 platforma të lidhura në të gjitha grupet e burimeve",
            "view": "accounts",
        },
        {
            "id": "publishing",
            "ready": settings["publishing_enabled"],
            "label": "Çelësi i publikimit",
            "detail": "I ndezur; vlejnë ende kontrollet për çdo klip"
            if settings["publishing_enabled"]
            else "I fikur — videot nuk dërgohen në platforma",
            "view": "settings",
        },
    ]
    remote = [
        worker
        for worker in snapshot["workers"]
        if worker["id"] != settings.get("analysis_worker_id")
        and not worker["paused"]
        and (worker["last_seen"] or 0) > time.time() - 30
        and "render" in worker["capabilities"]
    ]
    outdated = [
        worker["id"]
        for worker in remote
        if worker["hardware"].get("render_revision", 0) < 2
        or worker["hardware"].get("archive_protocol", 0) < 1
    ]
    return {
        "configured": all(check["ready"] for check in checks),
        "checks": checks,
        "platforms": platforms,
        "campaign_issues": campaign_issues,
        "worker_updates": outdated,
        "headline_style": "third_person_curiosity",
        "outbox": store.all("SELECT status,count(*) n FROM publications GROUP BY status"),
        "note": "Gatishmëria e konfigurimit nuk zëvendëson cilësinë, lejet dhe verifikimin e çdo publikimi.",
    }

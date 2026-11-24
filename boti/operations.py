"""Bounded, credential-free operational telemetry for the live studio."""

import json
import math
import time

from boti.storage_budget import ingest_reserve


def record_download(store, job_id, progress):
    value = {"at": time.time(), "phase": "download"}
    for name in ("downloaded_bytes", "total_bytes", "total_bytes_estimate", "speed", "eta"):
        number = progress.get(name)
        if type(number) in (int, float) and math.isfinite(number) and number >= 0:
            value[name] = number
    store.execute(
        "UPDATE jobs SET metadata=json_set(metadata,'$.ingest_progress',json(?)),updated=? WHERE id=?",
        (json.dumps(value), value["at"], job_id),
    )


def record_phase(store, job_id, phase, **counts):
    value = {"at": time.time(), "phase": phase, **counts}
    store.execute(
        "UPDATE jobs SET metadata=json_set(metadata,'$.ingest_progress',json(?)),updated=? WHERE id=?",
        (json.dumps(value), value["at"], job_id),
    )


def operations(store, snapshot):
    now, since = time.time(), time.time() - 86400
    tasks = store.all("""SELECT t.id,t.job_id,t.kind,t.task_key,t.worker_id,t.status,t.started,
        t.created,t.updated,t.attempts,t.error,t.lease_expires,
        coalesce(json_extract(t.payload,'$.offset'),c.start) offset,
        coalesce(json_extract(t.payload,'$.duration'),c.end-c.start) duration,
        json_extract(t.payload,'$.clip_id') clip_id,json_extract(c.metadata,'$.title') clip_title,
        j.title,j.provider,j.run_mode
        FROM tasks t JOIN jobs j ON j.id=t.job_id
        LEFT JOIN clips c ON c.id=json_extract(t.payload,'$.clip_id')
        WHERE t.status IN ('queued','running') AND j.status='processing'
        ORDER BY (t.status='running') DESC,t.created LIMIT 200""")
    for task in tasks:
        task["elapsed_seconds"] = max(0, now - task["started"]) if task["started"] else 0
        task["wait_seconds"] = max(0, now - task["created"])
    workers = store.all(
        """SELECT worker_id,kind,count(*) completed,avg(finished-started) average_seconds
        FROM tasks WHERE status='complete' AND finished>? AND started IS NOT NULL
        GROUP BY worker_id,kind""",
        (since,),
    )
    funnel = store.all(
        """SELECT j.run_mode,count(*) proposed,
        sum(c.status IN ('ready','archived')) accepted,sum(c.status='skipped') rejected,
        sum(c.status='technical_failed') failed,
        sum(c.status NOT IN ('ready','archived','skipped','technical_failed')) active
        FROM clips c JOIN jobs j ON j.id=c.job_id WHERE c.created>? AND c.status!='deleted' GROUP BY j.run_mode""",
        (since,),
    )
    sections = store.all(
        """SELECT j.run_mode,t.kind,count(*) completed
        FROM tasks t JOIN jobs j ON j.id=t.job_id WHERE t.finished>? AND t.status='complete'
        AND t.kind IN ('transcribe','analyze') GROUP BY j.run_mode,t.kind""",
        (since,),
    )
    reasons = {}
    for row in store.all(
        """SELECT j.run_mode,json_extract(q.result,'$.verdict.repair') repair,
        json_extract(q.result,'$.technical_passed') technical_passed
        FROM quality_reports q JOIN clips c ON c.id=q.clip_id JOIN jobs j ON j.id=c.job_id
        WHERE q.version=c.current_version AND q.passed=0 AND q.created>? AND c.created>?
        AND c.status IN ('skipped','technical_failed')
        AND NOT EXISTS(SELECT 1 FROM quality_reports newer WHERE newer.clip_id=q.clip_id
        AND newer.version=q.version AND (newer.created>q.created OR
        (newer.created=q.created AND newer.task_id>q.task_id)))""",
        (since, since),
    ):
        reason = row["repair"] or "none"
        if not row["technical_passed"] and reason == "none":
            reason = "technical"
        mode = reasons.setdefault(row["run_mode"], {})
        mode[reason] = mode.get(reason, 0) + 1
    activity = store.all(
        """SELECT t.id,t.job_id,t.kind,t.worker_id,t.status,t.finished,t.error,
        j.title,j.run_mode FROM tasks t JOIN jobs j ON j.id=t.job_id
        WHERE t.status IN ('complete','failed') AND t.finished>?
        ORDER BY t.finished DESC LIMIT 12""",
        (since,),
    )
    bottlenecks = []
    if snapshot["settings"]["paused"]:
        bottlenecks.append("Puna është pezulluar nga konfigurimi.")
    if snapshot["disk"]["paused"]:
        bottlenecks.append("Disku është nën prag; shkarkimet presin hapësirë.")
    downloads = [j for j in snapshot["jobs"] if j["status"] == "downloading"]
    if downloads and not any(t["status"] == "running" for t in tasks):
        bottlenecks.append(
            "Main-i po shkarkon ose përgatit VOD-in. Workerat marrin segmente pasi të përgatitet audioja."
        )
    for stage in snapshot["stages"]:
        if stage["queued"] and not stage["capable_workers"]:
            bottlenecks.append(f"{stage['kind']}: {stage['queued']} detyra presin një PC të përshtatshëm.")
    for job in snapshot["jobs"]:
        if job["status"] == "queued" and job["error"]:
            bottlenecks.append(job["error"][:300])
    storage = [
        {
            "job_id": j["id"],
            "required_free_gb": round(
                ingest_reserve(j["metadata"], store.config.root / "media" / j["id"]) / 1024**3
                + snapshot["settings"]["min_free_gb"],
                1,
            ),
        }
        for j in snapshot["jobs"]
        if j["status"] in {"queued", "downloading"} and j["metadata"].get("download_estimated_bytes")
    ]
    return {
        "tasks": tasks,
        "completed_by_worker": workers,
        "funnel_24h": funnel,
        "sections_24h": sections,
        "rejection_reasons_24h": reasons,
        "activity": activity,
        "bottlenecks": list(dict.fromkeys(bottlenecks))[:8],
        "task_list_limited": len(tasks) == 200,
        "storage_requirements": storage,
    }

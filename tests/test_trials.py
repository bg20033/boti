import json
import shutil
import time
from pathlib import Path

import pytest

from boti.campaigns import publish_allowed
from boti.db import Store, encode, uid
from boti.pipeline import Pipeline
from boti.rules import ClipRules
from boti.trials import prepare_sample, report


def new_trial(store, start=60, seconds=300, limit=5):
    return store.create_job(
        "https://twitch.tv/videos/123",
        "123",
        ["pc-1-g1"],
        trial={"start_seconds": start, "duration_seconds": seconds, "max_clips": limit},
    )[0]


def test_trial_and_production_identity_survive_restart_without_cross_mode_reuse(store):
    full, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    first = new_trial(store)
    assert first != full and new_trial(store) == first
    assert new_trial(store, start=120) != first
    reopened = Store(store.config)
    assert new_trial(reopened) == first
    assert reopened.one("SELECT run_mode FROM jobs WHERE id=?", (full,))["run_mode"] == "production"
    assert reopened.one("SELECT review_mode FROM jobs WHERE id=?", (first,))["review_mode"] == "automatic"
    with reopened.connect() as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize(
    "trial",
    [
        {"duration_seconds": 29},
        {"duration_seconds": 901},
        {"start_seconds": -1},
        {"max_clips": 2},
        {"max_clips": 6},
        {"duration_seconds": "Infinity"},
    ],
)
def test_trial_input_limits(client, admin_headers, trial):
    assert (
        client.post(
            "/api/jobs", headers=admin_headers, json={"url": "https://twitch.tv/videos/123", "trial": trial}
        ).status_code
        == 422
    )


def test_trial_queues_with_missing_workers_and_report_explains_why(client, store, admin_headers, monkeypatch):
    response = client.post(
        "/api/jobs", headers=admin_headers, json={"url": "https://twitch.tv/videos/123", "trial": {}}
    )
    assert response.status_code == 200
    job = response.json()["id"]
    calls = []
    pipeline = Pipeline(store)
    monkeypatch.setattr(pipeline, "ingest", lambda j: calls.append(j["id"]))
    pipeline.ingest_next()
    assert calls == []
    assert client.get(f"/api/jobs/{job}/trial-report").status_code == 401
    result = client.get(f"/api/jobs/{job}/trial-report", headers=admin_headers).json()
    assert not result["readiness"]["ready"] and "quality" in result["readiness"]["missing_capabilities"]
    full, _ = store.create_job("https://twitch.tv/videos/999", "999", ["pc-1-g1"])
    pipeline.ingest_next()
    assert calls == [full]  # A waiting trial does not starve normal work.
    store.heartbeat("pc-1", ["transcribe", "analyze", "render", "quality", "repair"])
    monkeypatch.setattr("boti.trials.shutil.which", lambda _: "/fake/binary")
    pipeline.ingest_next()
    assert calls[-1] == job


def test_completed_trial_delivers_no_outbox_even_with_publishing_enabled(store):
    job = new_trial(store)
    settings = store.settings()
    settings.publishing_enabled = True
    store.save_settings(settings)
    store.execute("UPDATE accounts SET status='connected'")
    clip = uid()
    now = time.time()
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,created,updated) VALUES (?,?,'pc-1-g1',2,32,90,?,?,?)",
        (clip, job, encode({"title": "Trial only", "reason": "Clear payoff"}), now, now),
    )
    path = store.config.root / "media" / "result.mp4"
    path.write_bytes(b"test result")
    asset = store.add_asset(job, path)
    pipeline = Pipeline(store)
    for _ in range(2):
        pipeline.deliver_clip(
            store.one("SELECT * FROM clips WHERE id=?", (clip,)),
            asset["id"],
            ["youtube", "instagram", "tiktok"],
        )
    assert store.one("SELECT count(*) n FROM publications")["n"] == 0
    row = store.one("SELECT * FROM clips WHERE id=?", (clip,))
    assert not publish_allowed(store, row)
    value = report(store, store.one("SELECT * FROM jobs WHERE id=?", (job,)))
    assert value["counts"]["ready"] == 1
    assert value["clips"][0]["source_start"] == 62 and value["clips"][0]["source_end"] == 92
    assert value["clips"][0]["selection_reason"] == "Clear payoff"
    assert value["clips"][0]["video_url"].endswith("/video")


def test_trial_candidate_count_is_a_cap_not_a_required_quota(store):
    job = new_trial(store, limit=3)
    store.execute(
        "UPDATE jobs SET status='processing',stage='analyze',metadata=? WHERE id=?",
        (encode({"duration": 300}), job),
    )
    candidates = [{"start": i * 40, "end": i * 40 + 30, "score": 90 - i, "title": str(i)} for i in range(6)]
    analysis = [
        {
            "payload": encode({"offset": 0, "duration": 300, "transcript": {"segments": []}}),
            "result": encode({"candidates": candidates}),
        }
    ]
    pipeline = Pipeline(store)
    pipeline.plan_clips(store.one("SELECT * FROM jobs WHERE id=?", (job,)), analysis)
    assert store.one("SELECT count(*) n FROM clips WHERE job_id=?", (job,))["n"] == 3
    empty = new_trial(store, start=600)
    store.execute("UPDATE jobs SET status='processing',stage='analyze' WHERE id=?", (empty,))
    analysis[0]["result"] = encode({"candidates": []})
    pipeline.plan_clips(store.one("SELECT * FROM jobs WHERE id=?", (empty,)), analysis)
    assert store.one("SELECT count(*) n FROM clips WHERE job_id=?", (empty,))["n"] == 0


@pytest.mark.parametrize("source_duration,returned_duration", [(3600, 60), (100, 60), (3600, 3600)])
def test_remote_trial_uses_only_download_range_and_reuses_sample(
    store, monkeypatch, source_duration, returned_duration
):
    job = new_trial(store, seconds=60)
    row = store.one("SELECT * FROM jobs WHERE id=?", (job,))
    requests = []

    class Downloader:
        def __init__(self, options):
            self.options = options
            assert list(options["download_ranges"]({"duration": 3600}, self)) == [
                {"start_time": 60, "end_time": 120}
            ]
            assert options["force_keyframes_at_cuts"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def prepare_filename(self, info):
            return self.options["outtmpl"].replace("%(ext)s", "mp4")

        def extract_info(self, url, download=False, ie_key=None):
            requests.append(download)
            if download:
                Path(self.prepare_filename({})).write_bytes(b"selected interval")
            return {"duration": source_duration, "uploader_id": "creator", "title": "Source"}

    monkeypatch.setattr("yt_dlp.YoutubeDL", Downloader)
    monkeypatch.setattr("boti.trials.duration", lambda _: returned_duration)
    if source_duration < 120 or returned_duration != 60:
        with pytest.raises(ValueError):
            prepare_sample(store, row, ClipRules(), lambda *args: True)
        assert requests == ([False] if source_duration < 120 else [False, True])
        assert not list((store.config.root / "media" / job).glob("*.mp4"))
        return
    first, meta = prepare_sample(store, row, ClipRules(), lambda *args: True)
    assert meta["source_offset"] == 60 and requests == [False, True]
    assert prepare_sample(store, row, ClipRules(), lambda *args: True)[0] == first
    assert requests == [False, True]


def test_sample_disk_pressure_stops_before_download(store, monkeypatch):
    from boti.pipeline import DownloadPaused

    job = new_trial(store)
    monkeypatch.setattr("yt_dlp.YoutubeDL", lambda _: pytest.fail("No download with low disk"))
    with pytest.raises(DownloadPaused):
        prepare_sample(
            store, store.one("SELECT * FROM jobs WHERE id=?", (job,)), ClipRules(), lambda *args: False
        )


def test_trial_does_not_count_as_production_throughput(store):
    from boti.cli import report as capacity_report

    job = new_trial(store)
    clip = uid()
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,status,created,updated) VALUES (?,?,'pc-1-g1',0,30,90,'{}','ready',?,?)",
        (clip, job, time.time(), time.time()),
    )
    assert capacity_report(store)["completed_clips"] == 0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_local_trial_real_segment_through_worker_api_without_publication(
    client, store, admin_headers, worker_headers, tmp_path
):
    from test_automatic import verdict

    from boti.media import duration, render, run, sha256

    source = store.config.root / "inbox" / "trial.mp4"
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=12",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "45",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(source),
        ]
    )
    response = client.post(
        "/api/jobs",
        headers=admin_headers,
        json={
            "file": source.name,
            "group_ids": ["pc-1-g1"],
            "trial": {"start_seconds": 10, "duration_seconds": 30, "max_clips": 3},
        },
    )
    assert response.status_code == 200, response.text
    job = response.json()["id"]
    store.heartbeat("pc-1", ["transcribe", "analyze", "render", "quality", "repair"])
    pipeline = Pipeline(store)
    pipeline.ingest_next()
    row = store.one("SELECT * FROM jobs WHERE id=?", (job,))
    meta = json.loads(row["metadata"])
    assert row["status"] == "processing" and abs(duration(store.config.path(meta["source"])) - 30) < 0.2
    assert meta["source_offset"] == 10

    def claim(kind):
        task = client.post("/api/worker/claim", headers=worker_headers["pc-1"]).json()["task"]
        assert task["kind"] == kind
        return task

    def complete(task, result):
        response = client.post(
            f"/api/worker/tasks/{task['id']}/complete",
            headers=worker_headers["pc-1"],
            json={"lease_token": task["lease_token"], "result": result},
        )
        assert response.status_code == 200, response.text

    task = claim("transcribe")
    complete(task, {"language": "en", "segments": []})
    pipeline.advance()
    task = claim("analyze")
    complete(
        task,
        {
            "candidates": [
                {"start": 2, "end": 22, "score": 90, "title": "Test moment", "reason": "Fixture payoff"}
            ]
        },
    )
    pipeline.advance()
    task = claim("render")
    segment = store.config.path(
        store.one("SELECT path FROM assets WHERE id=?", (task["payload"]["segment_id"],))["path"]
    )
    output = tmp_path / "render.mp4"
    rendered = render(segment, output, task["payload"], "libx264")
    upload = client.put(
        f"/api/worker/tasks/{task['id']}/output",
        content=output.read_bytes(),
        headers={
            **worker_headers["pc-1"],
            "X-Lease-Token": task["lease_token"],
            "X-SHA256": sha256(output),
            "Content-Length": str(output.stat().st_size),
        },
    )
    assert upload.status_code == 200, upload.text
    complete(task, {**rendered, **upload.json()})
    pipeline.advance()
    task = claim("quality")
    complete(task, verdict(True))
    pipeline = Pipeline(Store(store.config))
    pipeline.advance()
    pipeline.advance()
    report = client.get(f"/api/jobs/{job}/trial-report", headers=admin_headers).json()
    assert report["status"] == "complete" and report["counts"]["ready"] == 1
    assert report["clips"][0]["source_start"] == 12 and report["clips"][0]["source_end"] == 32
    assert len(report["clips"][0]["quality_reports"]) == 1
    assert report["processing_seconds"] is not None
    assert client.get(report["clips"][0]["video_url"], headers=admin_headers).status_code == 200
    assert store.one("SELECT count(*) n FROM publications")["n"] == 0


def test_migration_from_production_only_schema_keeps_job_and_task(store):
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    task = store.add_task(job, "transcribe", 0, {"offset": 0})
    with store.connect() as db:
        db.execute("DROP INDEX job_provider_video")
        for column in ("run_mode", "trial_start", "trial_seconds", "trial_limit", "started"):
            db.execute(f"ALTER TABLE jobs DROP COLUMN {column}")
        db.execute("CREATE UNIQUE INDEX job_provider_video ON jobs(provider,video_id)")
        db.execute("UPDATE settings SET value='6' WHERE key='schema_version'")
    reopened = Store(store.config)
    assert reopened.one("SELECT run_mode FROM jobs WHERE id=?", (job,))["run_mode"] == "production"
    assert reopened.one("SELECT job_id FROM tasks WHERE id=?", (task,))["job_id"] == job
    assert new_trial(reopened) != job
    assert reopened.one("SELECT value FROM settings WHERE key='schema_version'")["value"] == "7"


def test_trial_cleanup_keeps_output_and_report_but_removes_source(store):
    job = new_trial(store)
    directory = store.config.root / "media" / job
    directory.mkdir()
    for name in ("trial.mp4", "trial-source.json", "trial-download.mp4", "download.json", "output.mp4"):
        (directory / name).write_text("fixture")
    store.execute(
        "UPDATE jobs SET status='complete',completed=? WHERE id=?", (time.time() - 400 * 86400, job)
    )
    Pipeline(store).cleanup()
    assert not (directory / "trial.mp4").exists()
    assert not (directory / "trial-source.json").exists()
    assert not (directory / "trial-download.mp4").exists()
    assert (directory / "output.mp4").exists()
    assert report(store, store.one("SELECT * FROM jobs WHERE id=?", (job,)))["status"] == "complete"

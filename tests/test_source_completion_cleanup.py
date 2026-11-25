import json
import time

import pytest

from boti.db import uid
from boti.retention import cleanup_sources


def completed_source(store):
    settings = store.settings()
    settings.delete_source_after_complete = True
    store.save_settings(settings)
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    folder = store.config.root / "media" / job
    folder.mkdir()
    raw = []
    for name in ["source.mp4", "audio.wav", "chunk-0.wav", "proxy-0.mp4", "segment-old.mp4"]:
        path = folder / name
        path.write_bytes(b"temporary source bytes")
        store.add_asset(job, path)
        raw.append(path)
    final = folder / "final.mp4"
    final.write_bytes(b"keep this finished clip")
    asset = store.add_asset(job, final)
    clip = uid()
    now = time.time()
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,status,asset_id,created,updated) VALUES(?,?,'pc-1-g1',0,30,90,'{}','ready',?,?,?)",
        (clip, job, asset["id"], now, now),
    )
    store.execute("UPDATE jobs SET status='complete',completed=? WHERE id=?", (now, job))
    return job, clip, raw, final


def test_completed_source_is_deleted_immediately_but_clips_versions_and_inbox_survive(store):
    job, clip, raw, final = completed_source(store)
    old_version = raw[-1]  # A final video may even have a name that resembles a prepared segment.
    asset = store.one(
        "SELECT id FROM assets WHERE path=?", (str(old_version.relative_to(store.config.root)),)
    )
    store.execute(
        "INSERT INTO clip_versions(clip_id,version,metadata,asset_id,created) VALUES(?,1,'{}',?,0)",
        (clip, asset["id"]),
    )
    inbox = store.config.root / "inbox" / "original.mp4"
    inbox.write_bytes(b"owner input")
    store.execute("INSERT INTO campaigns(id,data,created,updated) VALUES('campaign','{}',0,0)")
    store.execute("INSERT INTO job_campaigns VALUES(?,'campaign',1,'{}')", (job,))
    result = cleanup_sources(store, job)
    assert result["jobs_cleaned"] == 1 and result["files_removed"] == 4
    assert all(not path.exists() for path in raw[:-1])
    assert old_version.is_file() and final.is_file() and inbox.read_bytes() == b"owner input"
    assert json.loads(store.one("SELECT metadata FROM jobs WHERE id=?", (job,))["metadata"])[
        "originals_cleaned"
    ]
    assert cleanup_sources(store, job)["files_removed"] == 0


@pytest.mark.parametrize("hold", ["active_job", "active_task", "unfinished_clip", "other_job_reader"])
def test_in_progress_work_prevents_source_deletion(store, hold):
    job, clip, raw, final = completed_source(store)
    if hold == "active_job":
        store.execute("UPDATE jobs SET status='processing' WHERE id=?", (job,))
    elif hold == "unfinished_clip":
        store.execute("UPDATE clips SET status='checking' WHERE id=?", (clip,))
    elif hold == "other_job_reader":
        other, _ = store.create_job("https://twitch.tv/videos/456", "456", ["pc-1-g1"])
        asset = store.one("SELECT id FROM assets WHERE job_id=? ORDER BY created LIMIT 1", (job,))
        store.add_task(other, "analyze", "shared-input", {}, asset_ids=[asset["id"]])
    else:
        store.add_task(job, "quality", "pending", {})
    assert cleanup_sources(store, job)["jobs_cleaned"] == 0
    assert all(path.is_file() for path in raw) and final.is_file()


def test_locked_file_is_retried_before_marking_source_cleaned(store, monkeypatch):
    job, clip, raw, final = completed_source(store)
    path_type = type(raw[0])
    unlink = path_type.unlink

    def locked(path, *args, **kwargs):
        if path == raw[0]:
            raise PermissionError("still open")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "unlink", locked)
    assert cleanup_sources(store, job)["retry_jobs"] == 1
    assert not json.loads(store.one("SELECT metadata FROM jobs WHERE id=?", (job,))["metadata"]).get(
        "originals_cleaned"
    )
    monkeypatch.setattr(path_type, "unlink", unlink)
    assert cleanup_sources(store, job)["jobs_cleaned"] == 1
    assert all(not path.exists() for path in raw) and final.is_file()

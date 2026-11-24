import json
import time

import pytest

from boti.db import encode, uid
from boti.media import captions_ass
from boti.styles import V1_FIELDS, EditStyle


def test_title_options_do_not_break_old_renderers_when_disabled():
    style = EditStyle(title_text="Unused", title_seconds=12)
    assert style.required_revision == 0
    assert set(style.render_options()) == V1_FIELDS
    assert EditStyle(title_enabled=True).required_revision == 2


@pytest.mark.parametrize("full,expected", [(False, "0:00:04.00"), (True, "0:00:20.00")])
def test_title_timing_appearance_and_text_are_safe(tmp_path, full, expected):
    path = tmp_path / "captions.ass"
    style = EditStyle(title_enabled=True, title_full=full, title_size="large", title_appearance="outline")
    captions_ass(
        [], path, 20, overlays={"hook": r"{\pos(0,0)}The match is over"}, editing=style.render_options()
    )
    script = path.read_text(encoding="utf-8-sig")
    hook = next(line for line in script.splitlines() if "Hook,," in line)
    assert f",0:00:00.00,{expected},Hook" in hook
    assert r"\fad(160,240)" in hook and r"\pos(0,0)" not in hook
    assert "Hook,Arial,82" in script


@pytest.fixture
def editable(store):
    job, _ = store.create_job(
        "https://twitch.tv/videos/123",
        "123",
        ["pc-1-g1"],
        review_mode="automatic",
        trial={"start_seconds": 0, "duration_seconds": 30, "max_clips": 3},
    )
    directory = store.config.root / "media" / job
    directory.mkdir()
    source = directory / "source.mp4"
    source.write_bytes(b"retained original")
    store.execute(
        "UPDATE jobs SET status='complete',stage='complete',metadata=?,completed=? WHERE id=?",
        (encode({"source": str(source.relative_to(store.config.root))}), time.time(), job),
    )
    clip = uid()
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,status,created,updated) VALUES (?,?,'pc-1-g1',0,20,90,?,'skipped',?,?)",
        (
            clip,
            job,
            encode({"title": "Original title", "start": 0, "end": 20, "score": 90}),
            time.time(),
            time.time(),
        ),
    )
    return job, clip, source


def test_title_edit_versions_and_requeues_for_quality_review(
    client, store, admin_headers, editable, monkeypatch
):
    from boti.pipeline import Pipeline

    job, clip, _ = editable
    store.execute("INSERT INTO reviews VALUES (?,1,'Old approval',?)", (clip, time.time()))
    url = f"/api/clips/{clip}/title"
    assert client.post(url, json={"text": "New title", "expected_version": 1}).status_code == 401
    response = client.post(url, headers=admin_headers, json={"text": "New title", "expected_version": 1})
    assert response.status_code == 200 and response.json()["version"] == 2
    current = store.one("SELECT * FROM clips WHERE id=?", (clip,))
    assert current["status"] == "preparing" and current["asset_id"] is None
    assert json.loads(current["metadata"])["title"] == "Original title"
    assert json.loads(current["metadata"])["top_title_override"] == "New title"
    assert store.one("SELECT status FROM jobs WHERE id=?", (job,))["status"] == "processing"
    assert store.one("SELECT * FROM reviews WHERE clip_id=?", (clip,)) is None
    old = store.one("SELECT metadata FROM clip_versions WHERE clip_id=? AND version=1", (clip,))
    assert "_human_review" not in json.loads(old["metadata"])
    assert store.one("SELECT message FROM events WHERE job_id=? AND message LIKE '%Old approval%'", (job,))
    assert (
        client.post(
            url, headers=admin_headers, json={"text": "Stale edit", "expected_version": 1}
        ).status_code
        == 409
    )
    assert (
        client.post(url, headers=admin_headers, json={"text": "New title", "expected_version": 2}).json()[
            "changed"
        ]
        is False
    )
    monkeypatch.setattr(
        "boti.pipeline.cut_segment", lambda src, dst, *args: dst.write_bytes(b"prepared segment")
    )
    pipeline = Pipeline(store)
    row = store.one("SELECT * FROM jobs WHERE id=?", (job,))
    pipeline.prepare_renders(row, editable[2])
    task = store.one("SELECT payload FROM tasks WHERE job_id=? AND kind='render'", (job,))
    payload = json.loads(task["payload"])
    assert payload["render_revision"] == 2 and payload["version"] == 2
    assert payload["top_title_override"] == "New title" and payload["editing"]["title_enabled"] is True


def test_missing_original_and_publication_block_editing(client, store, admin_headers, editable):
    job, clip, source = editable
    body = {"text": "New title", "expected_version": 1}
    source.unlink()
    assert client.post(f"/api/clips/{clip}/title", headers=admin_headers, json=body).status_code == 409
    source.write_bytes(b"original")
    account = store.one("SELECT id FROM accounts WHERE group_id='pc-1-g1' LIMIT 1")["id"]
    store.execute(
        "INSERT INTO publications(id,clip_id,account_id,platform,status,created,updated) VALUES (?,?,?,'youtube','queued',0,0)",
        (uid(), clip, account),
    )
    assert client.post(f"/api/clips/{clip}/title", headers=admin_headers, json=body).status_code == 409
    assert store.one("SELECT current_version FROM clips WHERE id=?", (clip,))["current_version"] == 1


def test_only_upgraded_renderer_claims_titled_work(store):
    settings = store.settings()
    settings.analysis_worker_id = "pc-1"
    store.save_settings(settings)
    store.heartbeat("pc-1", ["render", "analyze", "quality"], {"render_revision": 2})
    store.heartbeat("pc-2", ["render"], {"render_revision": 1})
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute("UPDATE jobs SET status='processing' WHERE id=?", (job,))
    old = store.add_task(job, "render", "old", {"render_revision": 1})
    titled = store.add_task(job, "render", "title", {"render_revision": 2})
    assert store.claim("pc-2")["id"] == old
    assert store.claim("pc-1")["id"] == titled

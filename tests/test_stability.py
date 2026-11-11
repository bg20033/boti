import json
import time

import pytest

from boti.pipeline import Pipeline
from boti.rules import ClipRules, source_allowed
from boti.sources import file_identity, legacy_file_identity, reconcile_file_identity

UUID = "01a1b7a3-f0e8-7254-aef0-2384158ccac2"


@pytest.mark.parametrize(
    "allowed,identities,expected",
    [
        ("deenthegreat", [f"https://kick.com/deenthegreat/videos/{UUID}"], True),
        ("deenthegreat", [f"https://kick.com/notdeenthegreat/videos/{UUID}"], False),
        ("deenthegreat", ["deenthegreat_backup"], False),
        ("deenthegreat", ["DEENTHEGREAT"], True),
        ("@deenthegreat", ["deenthegreat"], True),
        ("Fixture Show", ["Fixture Show"], True),
        ("Fixture Show", ["Another Fixture Show"], False),
        ("https://kick.com/deenthegreat/", [f"https://kick.com/deenthegreat/videos/{UUID}"], True),
        ("https://kick.com/deenthegreat", ["https://twitch.tv/deenthegreat", "deenthegreat"], False),
        ("https://kick.com/deenthegreat", ["https://kick.com/deenthegreat2"], False),
        ("https://kick.com/deenthegreat", ["https://kick.com.evil.test/deenthegreat"], False),
        ("https://kick.com/deenthegreat", ["https://kick.com/other?ref=deenthegreat"], False),
        (
            f"https://kick.com/deenthegreat/videos/{UUID}",
            [f"https://kick.com/deenthegreat/videos/{UUID}?t=4"],
            True,
        ),
        (
            f"https://kick.com/deenthegreat/videos/{UUID}",
            ["https://kick.com/deenthegreat/videos/01a1b7a3-f0e8-7254-aef0-2384158ccac3"],
            False,
        ),
        ("https://twitch.tv/name", ["https://www.twitch.tv/name"], True),
        ("https://www.youtube.com/@Creator", ["https://youtube.com/@creator"], True),
        ("https://www.youtube.com/channel/UCSomeID", ["https://youtube.com/channel/UCSomeID"], True),
        ("https://www.youtube.com/channel/UCSomeID", ["https://youtube.com/channel/UCsomeid"], False),
        ("https://youtube.com/watch?v=AbcDef12345", ["https://youtu.be/AbcDef12345"], True),
        ("https://youtube.com/watch?v=AbcDef12345", ["https://youtu.be/abcdef12345"], False),
    ],
)
def test_source_restrictions_are_exact_and_platform_scoped(allowed, identities, expected):
    assert source_allowed(ClipRules(allowed_sources=[allowed]), *identities) is expected


def test_empty_source_configuration_preserves_unrestricted_behavior():
    assert source_allowed(ClipRules(), "anything")
    assert not source_allowed(ClipRules(allowed_sources=["creator"]))


def colliding_files(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    prefix = b"0" * (8 * 1024 * 1024)
    a.write_bytes(prefix + b"A" * 1024)
    b.write_bytes(prefix + b"B" * 1024)
    assert legacy_file_identity(a) == legacy_file_identity(b)
    return a, b


def test_identity_hashes_tail_and_middle_and_is_name_independent(tmp_path):
    a, b = colliding_files(tmp_path)
    assert file_identity(a) != file_identity(b)
    b.write_bytes(a.read_bytes())
    assert file_identity(a) == file_identity(b)
    # Also cover the hole between the old 8 MB head/tail samples.
    for path, middle in [(a, b"A"), (b, b"B")]:
        path.write_bytes(b"0" * (8 * 1024 * 1024) + middle * 1024 + b"0" * (8 * 1024 * 1024))
    assert legacy_file_identity(a) == legacy_file_identity(b)
    assert file_identity(a) != file_identity(b)


def legacy_job(store, path, retained=True):
    job, _ = store.create_job("file:a.mp4", legacy_file_identity(path), ["pc-1-g1"], provider="file")
    if retained:
        directory = store.config.root / "media" / job
        directory.mkdir()
        original = directory / "source.mp4"
        original.write_bytes(path.read_bytes())
        store.execute(
            "UPDATE jobs SET metadata=? WHERE id=?",
            (json.dumps({"source": str(original.relative_to(store.config.root))}), job),
        )
    return job


def test_reimport_migrates_verified_legacy_identity_without_changing_job(store, tmp_path):
    a = tmp_path / "a.mp4"
    a.write_bytes(b"original video")
    job = legacy_job(store, a)
    identity = file_identity(a)
    reconcile_file_identity(store, a, identity)
    reconcile_file_identity(store, a, identity)
    assert store.create_job("file:a.mp4", identity, ["pc-1-g1"], provider="file") == (job, False)
    assert store.one("SELECT count(*) n FROM jobs")["n"] == 1


def test_legacy_collision_does_not_reuse_another_video_job(store, tmp_path):
    a, b = colliding_files(tmp_path)
    old = legacy_job(store, a)
    reconcile_file_identity(store, b, file_identity(b))
    new, created = store.create_job("file:b.mp4", file_identity(b), ["pc-1-g1"], provider="file")
    assert created and new != old
    assert store.one("SELECT video_id FROM jobs WHERE id=?", (old,))["video_id"] == file_identity(a)


def test_legacy_missing_original_blocks_ambiguous_reimport(store, tmp_path):
    path = tmp_path / "a.mp4"
    path.write_bytes(b"video")
    old = legacy_job(store, path, retained=False)
    with pytest.raises(ValueError, match="origjinali mungon"):
        reconcile_file_identity(store, path, file_identity(path))
    assert store.one("SELECT video_id FROM jobs WHERE id=?", (old,))["video_id"] == legacy_file_identity(path)


def test_file_import_api_dedup_and_immutable_original(client, store, admin_headers, monkeypatch):
    path = store.config.root / "inbox" / "original.mp4"
    path.write_bytes(b"original bytes")
    body = {"file": path.name, "group_ids": ["pc-1-g1"]}
    first = client.post("/api/jobs", headers=admin_headers, json=body).json()
    renamed = path.with_name("renamed.mp4")
    renamed.write_bytes(path.read_bytes())
    assert client.post("/api/jobs", headers=admin_headers, json={**body, "file": renamed.name}).json() == {
        "id": first["id"],
        "created": False,
    }
    monkeypatch.setattr("boti.pipeline.duration", lambda _: 30)
    monkeypatch.setattr("boti.pipeline.extract_audio", lambda src, dst: dst.write_bytes(b"audio"))
    monkeypatch.setattr("boti.pipeline.audio_slice", lambda src, dst, *args: dst.write_bytes(b"audio"))
    Pipeline(store).ingest_next()
    row = store.one("SELECT * FROM jobs WHERE id=?", (first["id"],))
    assert row["status"] == "processing"
    original = store.config.path(json.loads(row["metadata"])["source"])
    path.write_bytes(b"edited inbox")
    assert original.read_bytes() == b"original bytes"


def test_queued_inbox_mutation_stops_before_transcription(client, store, admin_headers):
    path = store.config.root / "inbox" / "original.mp4"
    path.write_bytes(b"original")
    job = client.post(
        "/api/jobs", headers=admin_headers, json={"file": path.name, "group_ids": ["pc-1-g1"]}
    ).json()["id"]
    path.write_bytes(b"changed")
    Pipeline(store).ingest_next()
    assert store.one("SELECT status FROM jobs WHERE id=?", (job,))["status"] == "failed"
    assert store.one("SELECT count(*) n FROM tasks")["n"] == 0


def test_runtime_readiness_uses_only_live_unpaused_workers(client, store, admin_headers):
    def snapshot():
        return client.get("/api/snapshot", headers=admin_headers).json()

    assert snapshot()["version"] == "0.7.0"
    assert not snapshot()["readiness"]["workers_ready"]
    store.heartbeat("pc-1", ["transcribe", "analyze", "render", "quality", "repair"])
    assert snapshot()["readiness"]["workers_ready"]
    store.execute("UPDATE workers SET paused=1 WHERE id='pc-1'")
    assert not snapshot()["readiness"]["workers_ready"]
    store.execute("UPDATE workers SET paused=0,last_seen=? WHERE id='pc-1'", (time.time() - 35,))
    assert not snapshot()["readiness"]["workers_ready"]


def test_saved_source_is_rechecked_after_restart_and_at_publication(store, client, admin_headers):
    from boti.campaigns import publish_allowed
    from boti.db import uid

    rules = {
        "name": "Restricted",
        "group_ids": ["pc-1-g1"],
        "status": "active",
        "rules": "Only this creator",
        "rules_confirmed": True,
        "automation_confirmed": True,
        "clip": {"allowed_sources": ["deenthegreat"]},
    }
    campaign = client.post("/api/campaigns", headers=admin_headers, json=rules).json()["id"]
    job, _ = store.create_job(
        f"https://kick.com/notdeenthegreat/videos/{UUID}", UUID, ["pc-1-g1"], campaign, provider="kick"
    )
    directory = store.config.root / "media" / job
    directory.mkdir()
    source = directory / "source.mp4"
    source.write_bytes(b"previously downloaded")
    (directory / "download.json").write_text(
        json.dumps(
            {
                "source": str(source.relative_to(store.config.root)),
                "metadata": {"channel": "kick:notdeenthegreat"},
            }
        )
    )
    Pipeline(store).ingest_next()
    assert "Burimi i ruajtur" in store.one("SELECT error FROM jobs WHERE id=?", (job,))["error"]
    clip = uid()
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,created,updated) VALUES (?,?,'pc-1-g1',0,30,90,'{}',0,0)",
        (clip, job),
    )
    store.execute("INSERT INTO reviews VALUES (?,1,?,0)", (clip, ""))
    assert not publish_allowed(store, store.one("SELECT * FROM clips WHERE id=?", (clip,)))

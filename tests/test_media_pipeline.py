import json
import shutil
import time

import httpx
import pytest

from boti.ai import LocalAI, select_candidates
from boti.media import captions_ass, loudness_filter, render, run, sha256
from boti.pipeline import Pipeline, relative_segments


def test_overlap_dedup_and_relative_captions():
    candidates = [
        {"start": 0, "end": 30, "score": 80},
        {"start": 4, "end": 34, "score": 90},
        {"start": 70, "end": 100, "score": 85},
    ]
    assert [c["score"] for c in select_candidates(candidates)] == [90, 85]
    result = relative_segments(
        [
            {
                "start": 8,
                "end": 16,
                "text": "Hello",
                "words": [
                    {"start": 9, "end": 11, "word": "Hello"},
                    {"start": 14, "end": 16, "word": "world"},
                ],
            }
        ],
        10,
        15,
    )
    assert result[0]["start"] == 0 and result[0]["end"] == 5
    assert result[0]["words"][0]["start"] == 0


def test_caption_text_cannot_inject_ass_commands(tmp_path):
    target = tmp_path / "captions.ass"
    captions_ass([{"start": 0, "end": 5, "text": r"{\pos(0,0)} Hi\Nthere"}], target, 20)
    line = target.read_text(encoding="utf-8-sig").split("Dialogue:")[1]
    assert "{" not in line and "\\" not in line


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg not installed")
@pytest.mark.parametrize("layout", ["mixed", "gaming"])
def test_real_video_end_to_end_with_worker_api_and_tiktok_export(
    client, store, worker_headers, tmp_path, layout, monkeypatch, admin_headers
):
    if layout == "gaming":
        store.execute(
            "INSERT INTO sources VALUES (?,?)",
            ("test", json.dumps({"mode": "gaming", "camera": {"x": 0, "y": 0, "w": 0.25, "h": 0.4}})),
        )
    source = tmp_path / "sample.mp4"
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
            "24",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(source),
        ]
    )
    # Exercise link -> provider adapter -> downloaded source, while external download is simulated.
    url = (
        "https://www.twitch.tv/videos/123"
        if layout == "mixed"
        else "https://kick.com/test/videos/01a1b7a3-f0e8-7254-aef0-2384158ccac2"
    )
    job_id = client.post(
        "/api/jobs", headers=admin_headers, json={"url": url, "group_ids": ["pc-1-g1"]}
    ).json()["id"]
    downloads = []

    class Downloader:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download=False, ie_key=None):
            assert ie_key == ("TwitchVod" if layout == "mixed" else "KickVOD")
            if download:
                downloads.append(url)
                shutil.copyfile(source, self.prepare_filename({}))
            return {"duration": 24, "uploader_id": "test", "title": "Synthetic integration fixture"}

        def prepare_filename(self, info):
            return self.options["outtmpl"].replace("%(ext)s", "mp4")

    monkeypatch.setattr("yt_dlp.YoutubeDL", Downloader)
    if layout == "gaming":
        store.execute("UPDATE sources SET id='kick:test' WHERE id='test'")
    store.heartbeat("pc-1", ["transcribe", "analyze", "render", "quality", "repair"])
    pipeline = Pipeline(store)
    pipeline.ingest_next()
    assert len(downloads) == 1

    def claim():
        result = client.post("/api/worker/claim", headers=worker_headers["pc-1"])
        assert result.status_code == 200
        return result.json()["task"]

    def complete(task, result):
        response = client.post(
            f"/api/worker/tasks/{task['id']}/complete",
            headers=worker_headers["pc-1"],
            json={"lease_token": task["lease_token"], "result": result},
        )
        assert response.status_code == 200, response.text

    task = claim()
    assert task["kind"] == "transcribe"
    text = "This is a synthetic clip for testing accurate captions, clear line breaks, and the final video composition."
    words = [{"start": 1 + i, "end": 1 + i + 0.85, "word": word} for i, word in enumerate(text.split())]
    complete(
        task,
        {"language": "en", "segments": [{"start": 1, "end": words[-1]["end"], "text": text, "words": words}]},
    )
    pipeline.advance()
    task = claim()
    assert task["kind"] == "analyze"

    def ollama(request):
        if request.url.path.endswith("generate"):
            return httpx.Response(200, json={"done": True})
        body = json.loads(request.content)
        result = {"approved": True, "score": 90, "reason": "Synthetic fixture"}
        if "repair" in body["format"]["properties"]:
            result = {"approved": True, "score": 90, "reasons": ["Synthetic fixture"], "repair": "none"}
        if "candidates" in body["format"]["properties"]:
            result = {
                "candidates": [
                    {
                        "start": 2,
                        "end": 22,
                        "score": 90,
                        "title": "Integration fixture",
                        "caption": "Only test material",
                        "hashtags": ["test"],
                        "content_type": layout,
                    }
                ]
            }
        return httpx.Response(200, json={"message": {"content": json.dumps(result)}})

    paths = {
        asset["id"]: store.config.path(
            store.one("SELECT path FROM assets WHERE id=?", (asset["id"],))["path"]
        )
        for asset in task["assets"]
    }
    payload = task["payload"]
    result = LocalAI(client=httpx.Client(transport=httpx.MockTransport(ollama))).analyze(
        paths[payload["audio_id"]],
        paths[payload["proxy_id"]],
        payload["transcript"],
        payload["duration"],
        tmp_path,
    )
    complete(task, result)
    pipeline.advance()
    task = claim()
    assert task["kind"] == "render" and task["payload"]["group_id"] == "pc-1-g1"
    asset_id = task["payload"]["segment_id"]
    response = client.get(
        f"/api/worker/tasks/{task['id']}/assets/{asset_id}",
        headers={**worker_headers["pc-1"], "X-Lease-Token": task["lease_token"]},
    )
    segment = tmp_path / "segment.mp4"
    segment.write_bytes(response.content)
    output = tmp_path / "output.mp4"
    render_result = render(segment, output, task["payload"], "libx264")
    assert render_result["layout"] == ("camera_game" if layout == "gaming" else "fit")
    assert render_result["audio"]["mode"].startswith("two_pass")
    assert r"{\c&H66E8FF&}" in (tmp_path / "captions.ass").read_text(encoding="utf-8-sig")
    _, measured = loudness_filter(output, render_result["duration"])
    assert abs(measured["input_lufs"] - (-16)) < 1
    # A full decode and format check is performed again on the coordinator.
    upload = client.put(
        f"/api/worker/tasks/{task['id']}/output",
        content=output.read_bytes(),
        headers={
            **worker_headers["pc-1"],
            "X-Lease-Token": task["lease_token"],
            "X-SHA256": sha256(output),
            "Content-Length": str(output.stat().st_size),
            "Content-Type": "video/mp4",
        },
    )
    assert upload.status_code == 200, upload.text
    from boti.archive import prune_cache, request_restore, retain

    archive_folder = tmp_path / "mint-archive"
    complete(task, {**upload.json(), "archive": retain(output, archive_folder)})
    pipeline.advance()
    task = claim()
    assert task["kind"] == "quality"
    from types import SimpleNamespace

    from boti.quality import check_rendered

    expected = task["payload"]["metadata"]["segments"]
    monkeypatch.setattr("boti.ai.transcribe", lambda *args, **kwargs: {"segments": expected})
    result = check_rendered(
        LocalAI(client=httpx.Client(transport=httpx.MockTransport(ollama))),
        output,
        task["payload"],
        tmp_path,
        SimpleNamespace(whisper_model="fixture", whisper_device="cpu"),
    )
    assert result["technical_passed"] and result["technical"]["caption_alignment"]["passed"]
    from PIL import Image

    with Image.open(tmp_path / "final-review.jpg") as board:
        assert board.size == (1080, 1328)  # Portrait captions must remain legible to the vision review.
    complete(task, result)
    pipeline = Pipeline(store)  # restart before accepting quality, without human review
    pipeline.advance()
    assert store.one("SELECT count(*) n FROM reviews")["n"] == 0
    assert store.one("SELECT count(*) n FROM quality_reports WHERE passed=1")["n"] == 1
    prepared_asset = store.one("SELECT * FROM assets WHERE id=?", (asset_id,))
    assert not store.config.path(prepared_asset["path"]).exists()
    clip = store.one("SELECT * FROM clips WHERE job_id=?", (job_id,))
    assert clip["status"] == "ready"
    assert store.one("SELECT status FROM jobs WHERE id=?", (job_id,))["status"] == "complete"
    assert store.one("SELECT count(*) n FROM publications WHERE clip_id=?", (clip["id"],))["n"] == 4
    assert (
        store.one("SELECT status FROM publications WHERE clip_id=? AND platform='tiktok'", (clip["id"],))[
            "status"
        ]
        == "manual_ready"
    )
    export = store.config.root / "exports" / "pc-1-g1" / clip["id"]
    assert (export / "video.mp4").exists() and (
        export / "caption.txt"
    ).read_text() == "Only test material\n\n#test"
    assert json.loads((export / "metadata.json").read_text())["group_id"] == "pc-1-g1"
    # Real FFmpeg output survives main-cache eviction and outbound worker restoration.
    final_asset = store.one("SELECT * FROM assets WHERE id=?", (clip["asset_id"],))
    store.execute("UPDATE worker_archives SET accessed=0")
    prune_cache(store)
    assert not store.config.path(final_asset["path"]).exists()
    assert (archive_folder / f"{final_asset['sha256']}.mp4").is_file()
    assert not request_restore(store, final_asset)
    restoration = client.post("/api/worker/archive/claim", headers=worker_headers["pc-1"]).json()["restore"]
    restored = client.put(
        f"/api/worker/archive/{final_asset['id']}/restore",
        content=output.read_bytes(),
        headers={**worker_headers["pc-1"], "X-Restore-Token": restoration["token"]},
    )
    assert restored.status_code == 200
    assert client.get(f"/api/clips/{clip['id']}/video", headers=admin_headers).content == output.read_bytes()
    assert client.get(f"/api/clips/{clip['id']}/tiktok.zip", headers=admin_headers).status_code == 200
    # Cleanup must retain an output while any platform is still waiting.
    store.execute("UPDATE clips SET updated=? WHERE id=?", (time.time() - 40 * 86400, clip["id"]))
    pipeline.cleanup()
    assert (export / "video.mp4").exists()
    store.execute("UPDATE publications SET status='published' WHERE clip_id=?", (clip["id"],))
    store.execute("UPDATE jobs SET completed=? WHERE id=?", (time.time() - 8 * 86400, job_id))
    pipeline.cleanup()
    assert not (export / "video.mp4").exists()
    assert store.one("SELECT status FROM clips WHERE id=?", (clip["id"],))["status"] == "archived"

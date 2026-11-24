import io
import json
import shutil
from types import SimpleNamespace

import httpx
import pytest
from test_automatic import automatic_job, finish_quality

from boti.ai import LocalAI
from boti.campaigns import Campaign, publish_allowed
from boti.config import Settings
from boti.db import encode
from boti.media import captions_ass, render, run, sha256
from boti.pipeline import Pipeline
from boti.quality import repair_metadata
from boti.rules import ClipRules, chunking, compliance, final_caption, parse_rules_text
from boti.sources import inbox_file, vod_url

RULES_TEXT = """Clip requirements
- Clips must be 15-45 seconds long.
- Min 10K views to qualify. $1.50 per 100K views.
- Include #KickClips and tag @deenthegreat in the caption.
- English only. Captions are required.
- Max 2 accounts per platform. TikTok and Instagram only.
- No gambling content. Add the campaign logo as a watermark.
"""


def campaign(client, headers, **clip):
    body = {
        "name": "Rules test",
        "group_ids": ["pc-1-g1"],
        "status": "active",
        "rules": "Real campaign rules",
        "rules_confirmed": True,
        "automation_confirmed": True,
        "clip": clip,
    }
    response = client.post("/api/campaigns", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_clip_rules_normalise_and_reject_impossible_ranges():
    rules = ClipRules(
        required_hashtags=["#Kick", "kick", "#clips!"],
        required_mentions=["@Deen.TheGreat"],
        banned_words=["  Gambling ", "gambling"],
        watermark_text=r"{\pos(0,0)}@me",
    )
    assert rules.required_hashtags == ["Kick", "clips"]
    assert rules.required_mentions == ["Deen.TheGreat"]
    assert rules.banned_words == ["gambling"]
    assert "{" not in rules.watermark_text and "\\" not in rules.watermark_text
    for bad in ({"min_seconds": 30, "max_seconds": 32}, {"max_seconds": 181}, {"logo_sha256": "abc"}):
        with pytest.raises(ValueError):
            ClipRules(**bad)


def test_pasted_campaign_rules_become_suggestions():
    result = parse_rules_text(RULES_TEXT)
    assert result["clip"] == {
        "min_seconds": 15,
        "max_seconds": 45,
        "required_hashtags": ["KickClips"],
        "required_mentions": ["deenthegreat"],
        "captions": True,
        "language": "en",
    }
    assert result["campaign"] == {
        "min_post_views": 10000,
        "rate": "1.50",
        "rate_unit": 100000,
        "max_accounts_per_platform": 2,
        "platforms": ["tiktok", "instagram"],
    }
    assert any("logo" in note for note in result["notes"])
    assert any("No gambling content" in note for note in result["notes"])
    # Every suggestion must be acceptable to the models the operator then saves.
    ClipRules(**result["clip"])
    assert parse_rules_text("Up to 1 minute. At least 20 seconds. $2 per 1,000 views")["clip"] == {
        "min_seconds": 20,
        "max_seconds": 60,
    }
    assert parse_rules_text("Be nice.") == {"clip": {}, "campaign": {}, "notes": []}
    assert "max_seconds" not in parse_rules_text("Max 1M views per clip")["clip"]


def test_post_text_always_carries_required_parts_and_platform_limits():
    rules = ClipRules(required_hashtags=["kick"], required_mentions=["streamer"], caption_suffix="#ad")
    meta = {"title": "T", "caption": "x" * 5000, "hashtags": ["kick", "funny"]}
    text = final_caption(meta, rules, "instagram")
    assert len(text) <= 2200 and text.endswith("@streamer\n\n#ad\n\n#kick #funny")
    assert final_caption({"title": "Only title"}, None) == "Only title"


def test_compliance_separates_fixable_render_issues_from_content_issues():
    rules = ClipRules(min_seconds=10, max_seconds=30, banned_words=["casino"], watermark_text="@me")
    meta = {"title": "Clean", "segments": [{"text": "We went to the Casino, twice."}]}
    result = compliance(meta, rules, 45, {"overlays": {"captions": True, "watermark": ""}})
    assert {i["code"]: i["fixable"] for i in result["issues"]} == {
        "duration": False,
        "banned_words": False,
        "watermark": True,
    }
    clean = {"title": "Clean", "segments": [{"text": "A casinoesque word is not the banned word."}]}
    assert compliance(clean, rules, 20, {"overlays": {"captions": True, "watermark": "@me"}})["passed"]
    assert compliance(clean, rules, 20)["passed"]  # Before rendering there is no overlay evidence to judge.


def test_analysis_windows_overlap_by_a_whole_clip():
    settings = Settings()
    assert chunking(settings, ClipRules()) == (300, 60)
    chunk, overlap = chunking(settings, ClipRules(min_seconds=60, max_seconds=180))
    assert overlap == 180 and chunk == 540 and chunk - overlap >= 120


@pytest.mark.parametrize(
    "url,video",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ],
)
def test_youtube_long_form_links(url, video):
    assert vod_url(url) == (f"https://www.youtube.com/watch?v={video}", "youtube", video)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/@channel",
        "https://www.youtube.com/watch?v=short",
        "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
        "http://youtu.be/dQw4w9WgXcQ",
    ],
)
def test_reject_non_video_youtube_links(url):
    with pytest.raises(ValueError):
        vod_url(url)


def test_inbox_accepts_only_plain_video_names(tmp_path):
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "show.mp4").write_bytes(b"x")
    (tmp_path / "secret.mp4").write_bytes(b"x")
    assert inbox_file(tmp_path, "show.mp4").name == "show.mp4"
    for name in ("../secret.mp4", "missing.mp4", "show.txt", ".hidden.mp4", "/etc/passwd", ""):
        with pytest.raises(ValueError):
            inbox_file(tmp_path, name)


def test_rules_api_logo_and_legacy_documents(client, store, admin_headers):
    from PIL import Image

    assert client.post("/api/campaigns/parse-rules", json={"text": "x"}).status_code == 401
    parsed = client.post("/api/campaigns/parse-rules", headers=admin_headers, json={"text": RULES_TEXT})
    assert parsed.json()["clip"]["max_seconds"] == 45
    assert (
        client.post(
            "/api/campaigns", headers=admin_headers, json={"name": "x", "clip": {"min_seconds": 2}}
        ).status_code
        == 422
    )
    # A client cannot point a campaign at an image the main never verified.
    identity = campaign(client, admin_headers, logo_sha256="a" * 64, watermark_text="@me")
    row = store.one("SELECT * FROM campaigns WHERE id=?", (identity,))
    assert json.loads(row["data"])["clip"]["logo_sha256"] == "" and row["revision"] == 1

    buffer = io.BytesIO()
    Image.new("RGB", (400, 200), "#ff0000").save(buffer, "JPEG")
    upload = client.put(f"/api/campaigns/{identity}/logo", headers=admin_headers, content=buffer.getvalue())
    assert upload.status_code == 200, upload.text
    digest = upload.json()["logo_sha256"]
    stored = store.config.root / "media" / "logos" / f"{digest}.png"
    assert sha256(stored) == digest and stored.read_bytes()[:4] == b"\x89PNG"
    assert store.one("SELECT revision FROM campaigns WHERE id=?", (identity,))["revision"] == 2
    assert client.get(f"/api/campaigns/{identity}/logo", headers=admin_headers).content == stored.read_bytes()
    assert (
        client.put(
            f"/api/campaigns/{identity}/logo", headers=admin_headers, content=b"not an image"
        ).status_code
        == 422
    )
    # Saving the form keeps the uploaded logo and does not invent a revision for identical rules.
    data = client.get("/api/campaigns", headers=admin_headers).json()[0]["data"]
    data["clip"]["logo_sha256"] = ""
    assert client.put(f"/api/campaigns/{identity}", headers=admin_headers, json=data).status_code == 200
    row = store.one("SELECT * FROM campaigns WHERE id=?", (identity,))
    assert json.loads(row["data"])["clip"]["logo_sha256"] == digest and row["revision"] == 2
    assert client.delete(f"/api/campaigns/{identity}/logo", headers=admin_headers).status_code == 200
    assert store.one("SELECT revision FROM campaigns WHERE id=?", (identity,))["revision"] == 3

    # A campaign saved before 0.5 has no clip rules: it reads as the defaults, without a new revision.
    legacy = json.loads(row["data"])
    legacy.pop("clip")
    store.execute("UPDATE campaigns SET data=?,revision=7 WHERE id=?", (json.dumps(legacy), identity))
    data = client.get("/api/campaigns", headers=admin_headers).json()[0]["data"]
    assert client.put(f"/api/campaigns/{identity}", headers=admin_headers, json=data).status_code == 200
    assert store.one("SELECT revision FROM campaigns WHERE id=?", (identity,))["revision"] == 7
    assert Campaign.model_validate(legacy).clip == ClipRules()


def test_campaign_limits_reach_selection_and_banned_words_are_exact(client, store, admin_headers):
    identity = campaign(
        client, admin_headers, min_seconds=8, max_seconds=15, banned_words=["casino"], max_clips_per_video=1
    )
    job_id, _ = store.create_job("https://twitch.tv/videos/5", "5", ["pc-1-g1"], identity)
    store.execute(
        "UPDATE jobs SET metadata=?,stage='analyze' WHERE id=?", (encode({"duration": 300}), job_id)
    )
    job = store.one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def segment(start, text):
        return {"start": start, "end": start + 10, "text": text, "words": []}

    def candidate(start, end, score, title):
        return {"start": start, "end": end, "score": score, "title": title, "content_type": "talk"}

    analysis = [
        {
            "payload": encode(
                {
                    "offset": 0,
                    "duration": 300,
                    "transcript": {
                        "segments": [
                            segment(0, "The best moment."),
                            segment(40, "At the casino."),
                            segment(80, "Fine."),
                        ]
                    },
                }
            ),
            "result": encode(
                {
                    "candidates": [
                        candidate(40, 52, 99, "Banned"),
                        candidate(0, 12, 95, "Best"),
                        candidate(80, 92, 90, "Second"),
                        candidate(120, 150, 98, "Too long for this campaign"),
                    ]
                }
            ),
        }
    ]
    Pipeline(store).plan_clips(job, analysis)
    clips = store.all("SELECT * FROM clips WHERE job_id=?", (job_id,))
    assert [json.loads(c["metadata"])["title"] for c in clips] == ["Best"]
    assert "fjalë të ndaluara" in store.one("SELECT message FROM events ORDER BY id DESC LIMIT 1")["message"]


def test_rule_overlay_change_rerenders_once_and_content_violations_skip(
    client, store, admin_headers, monkeypatch
):
    identity = campaign(client, admin_headers, watermark_text="@new", banned_words=["casino"])
    job, clip = automatic_job(store, monkeypatch, identity)
    pipeline = Pipeline(store)
    pipeline.advance()

    def finish_render(watermark):
        task = store.claim("pc-1")
        assert task["kind"] == "render" and task["payload"]["rules"]["watermark_text"] == "@new"
        path = store.config.root / "media" / job / f"{task['id']}.mp4"
        path.write_bytes(b"output" + str(task["payload"]["version"]).encode())
        asset = store.add_asset(job, path, task["id"])
        result = {"asset_id": asset["id"], "overlays": {"captions": True, "watermark": watermark}}
        store.finish(task["id"], "pc-1", task["lease_token"], result=result)

    finish_render("@old")  # Bytes rendered under an earlier watermark rule.
    pipeline.advance()
    finish_quality(store, True)  # The model likes it; the main still refuses the stale overlay.
    pipeline.advance()
    row = store.one("SELECT * FROM clips WHERE id=?", (clip,))
    assert row["status"] == "correcting" and "watermark" in row["quality_reason"]
    report = json.loads(store.one("SELECT result FROM quality_reports WHERE clip_id=?", (clip,))["result"])
    assert report["compliance"]["issues"][0]["code"] == "watermark"
    pipeline.advance()
    task = store.claim("pc-1")
    assert task["kind"] == "repair" and task["payload"]["repair"] == "layout"
    store.finish(task["id"], "pc-1", task["lease_token"], result=repair_metadata(task["payload"], None, None))
    pipeline.advance()
    pipeline.advance()
    finish_render("@new")
    pipeline.advance()
    finish_quality(store, True)
    pipeline.advance()
    row = store.one("SELECT * FROM clips WHERE id=?", (clip,))
    assert row["status"] == "ready" and row["current_version"] == 2
    assert publish_allowed(store, row)
    shown = client.get("/api/clips", headers=admin_headers).json()["clips"][0]
    assert shown["compliance"]["passed"] and shown["post_text"] == "fixture"

    # What was said cannot be repaired by rendering again: no second version is spent on it.
    store.execute("UPDATE jobs SET source_id='twitch:other',video_id='other' WHERE id=?", (job,))
    job, clip = automatic_job(store, monkeypatch, identity)
    meta = json.loads(store.one("SELECT metadata FROM clips WHERE id=?", (clip,))["metadata"])
    meta["segments"] = [{"start": 0, "end": 5, "text": "Casino night", "words": []}]
    store.execute("UPDATE clips SET metadata=? WHERE id=?", (encode(meta), clip))
    pipeline.advance()
    finish_render("@new")
    pipeline.advance()
    finish_quality(store, True)
    pipeline.advance()
    row = store.one("SELECT * FROM clips WHERE id=?", (clip,))
    assert row["status"] == "skipped" and row["current_version"] == 1
    assert not publish_allowed(store, row)


def test_daily_post_cap_holds_new_uploads_for_that_account(client, store, admin_headers, monkeypatch):
    from boti.publishing import Publisher

    identity = campaign(client, admin_headers, max_posts_per_account_daily=1)
    job, clip = automatic_job(store, monkeypatch, identity)
    account = store.one("SELECT id FROM accounts WHERE group_id='pc-1-g1' AND platform='youtube'")["id"]
    store.execute("UPDATE accounts SET status='connected',credentials='x' WHERE id=?", (account,))
    other = "other-clip"
    store.execute(
        "INSERT INTO clips(id,job_id,group_id,start,end,score,metadata,created,updated) VALUES (?,?,'pc-1-g1',0,30,90,'{}',0,0)",
        (other, job),
    )
    import time

    store.execute(
        "INSERT INTO publications(id,clip_id,account_id,platform,status,created,updated) VALUES ('done',?,?,'youtube','published',0,?)",
        (other, account, time.time()),
    )
    store.execute(
        "INSERT INTO publications(id,clip_id,account_id,platform,status,created,updated) VALUES ('next',?,?,'youtube','queued',0,0)",
        (clip, account),
    )
    settings = store.settings()
    settings.publishing_enabled = True
    store.save_settings(settings)
    monkeypatch.setattr("boti.campaigns.publish_allowed", lambda *args: True)
    publisher = Publisher(store, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    publisher.tick()
    held = store.one("SELECT * FROM publications WHERE id='next'")
    assert held["status"] == "queued" and "Kufiri ditor" in held["error"] and held["not_before"] > time.time()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg not installed")
def test_local_file_is_cut_and_branded_by_campaign_rules(
    client, store, worker_headers, tmp_path, monkeypatch, admin_headers
):
    from PIL import Image

    identity = campaign(
        client,
        admin_headers,
        min_seconds=8,
        max_seconds=15,
        watermark_text="@fixture",
        watermark_position="bottom_right",
        logo_position="top_left",
        hook_title=True,
        required_hashtags=["KickClips"],
        required_mentions=["streamer"],
        caption_suffix="#ad",
        allowed_sources=["Fixture Show"],
    )
    logo = io.BytesIO()
    Image.new("RGB", (400, 200), "#ff0000").save(logo, "PNG")
    digest = client.put(
        f"/api/campaigns/{identity}/logo", headers=admin_headers, content=logo.getvalue()
    ).json()["logo_sha256"]
    source = store.config.root / "inbox" / "episode 12.mp4"
    run(
        [
            *"ffmpeg -v error -y -f lavfi -i color=c=0x202020:size=640x360:rate=12 -f lavfi".split(),
            *"-i sine=frequency=440:sample_rate=16000 -t 24 -c:v libx264 -preset ultrafast -c:a aac".split(),
            str(source),
        ]
    )
    listed = client.get("/api/inbox", headers=admin_headers).json()["files"]
    assert [f["name"] for f in listed] == ["episode 12.mp4"]
    request = {
        "file": "episode 12.mp4",
        "source_label": "Not the campaign's show",
        "campaign_id": identity,
        "group_ids": ["pc-1-g1"],
    }
    refused = client.post("/api/jobs", headers=admin_headers, json=request).json()["id"]
    store.heartbeat("pc-1", ["transcribe", "analyze", "render", "quality", "repair"])
    pipeline = Pipeline(store)
    pipeline.ingest_next()
    failed = store.one("SELECT status,error FROM jobs WHERE id=?", (refused,))
    assert failed["status"] == "failed" and "burimet e lejuara" in failed["error"]
    # The refused attempt is the same file: correct the label on the existing record and retry it.
    store.execute(
        "UPDATE jobs SET status='queued',metadata=? WHERE id=?",
        (encode({"source_label": "Fixture Show"}), refused),
    )
    assert client.post("/api/jobs", headers=admin_headers, json=request).json() == {
        "id": refused,
        "created": False,
    }
    assert (
        client.post("/api/jobs", headers=admin_headers, json={**request, "file": "../x.mp4"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/jobs", headers=admin_headers, json={**request, "url": "https://youtu.be/dQw4w9WgXcQ"}
        ).status_code
        == 422
    )
    job_id = refused
    pipeline.ingest_next()
    job = store.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert job["status"] == "processing" and job["provider"] == "file"
    assert json.loads(job["metadata"])["channel"] == "file:Fixture Show"

    def claim(kind):
        task = client.post("/api/worker/claim", headers=worker_headers["pc-1"]).json()["task"]
        assert task and task["kind"] == kind
        return task

    def complete(task, result, status=200):
        response = client.post(
            f"/api/worker/tasks/{task['id']}/complete",
            headers=worker_headers["pc-1"],
            json={"lease_token": task["lease_token"], "result": result},
        )
        assert response.status_code == status, response.text

    def asset_file(task, asset_id, name):
        response = client.get(
            f"/api/worker/tasks/{task['id']}/assets/{asset_id}",
            headers={**worker_headers["pc-1"], "X-Lease-Token": task["lease_token"]},
        )
        assert response.status_code == 200
        (tmp_path / name).write_bytes(response.content)
        return tmp_path / name

    task = claim("transcribe")
    text = "A short campaign moment with a clear ending."
    words = [{"start": 3 + i, "end": 3.8 + i, "word": w} for i, w in enumerate(text.split())]
    complete(task, {"language": "en", "segments": [{"start": 3, "end": 11, "text": text, "words": words}]})
    pipeline.advance()
    task = claim("analyze")
    assert task["payload"]["limits"]["min_seconds"] == 8 and task["payload"]["limits"]["max_seconds"] == 15
    prompts = []

    def ollama(request):
        if request.url.path.endswith("generate"):
            return httpx.Response(200, json={"done": True})
        body = json.loads(request.content)
        prompts.append(body["messages"][1]["content"])
        result = {
            "approved": True,
            "score": 90,
            "reason": "Fixture",
            "headline": {
                "title": "The clear ending",
                "caption": "A moment",
                "evidence": "A short campaign moment with a clear ending.",
                "third_person": True,
                "grounded": True,
            },
        }
        if "repair" in body["format"]["properties"]:
            result = {"approved": True, "score": 90, "reasons": ["Fixture"], "repair": "none"}
        if "candidates" in body["format"]["properties"]:
            item = {"score": 90, "title": "The clear ending", "caption": "A moment", "hashtags": ["funny"]}
            result = {"candidates": [{**item, "start": 2, "end": 13}, {**item, "start": 0, "end": 24}]}
        return httpx.Response(200, json={"message": {"content": json.dumps(result)}})

    ai = LocalAI(client=httpx.Client(transport=httpx.MockTransport(ollama)))
    payload = task["payload"]
    result = ai.analyze(
        asset_file(task, payload["audio_id"], "audio.wav"),
        asset_file(task, payload["proxy_id"], "proxy.mp4"),
        payload["transcript"],
        payload["duration"],
        tmp_path,
        payload["threshold"],
        payload["limits"],
    )
    assert "each 8 to 15 seconds" in prompts[0] and "Real campaign rules" in prompts[0]
    assert len(result["candidates"]) == 1  # The 24-second proposal is outside the campaign's range.
    too_long = {**result["candidates"][0], "start": 0, "end": 24}
    complete(task, {"candidates": [too_long]}, 422)  # The main enforces the range against any worker.
    complete(task, result)
    pipeline.advance()

    task = claim("render")
    payload = task["payload"]
    assert payload["rules"]["logo_sha256"] == digest and payload["rules"]["hook_title"] is True
    segment = asset_file(task, payload["segment_id"], "segment.mp4")
    logo_file = asset_file(task, payload["logo_id"], "logo.png")
    output = tmp_path / "output.mp4"
    with pytest.raises(ValueError):
        render(segment, output, payload, "libx264")  # Never a campaign clip without its required logo.
    report = render(segment, output, payload, "libx264", logo_file)
    assert report["overlays"] == {"captions": True, "watermark": "@fixture", "logo": digest, "hook": True}
    assert 8 <= report["duration"] <= 15.5
    script = (tmp_path / "captions.ass").read_text(encoding="utf-8-sig")
    assert r"Mark,,60,60,230,,{\an3}@fixture" in script and "Hook,,0,0,0,," in script
    frame = tmp_path / "frame.png"
    run(["ffmpeg", "-v", "error", "-y", "-ss", "5", "-i", str(output), "-frames:v", "1", str(frame)])
    with Image.open(frame) as image:
        red, green, blue = image.convert("RGB").getpixel((60 + 130, 150 + 65))  # Centre of the logo box.
        assert red > 180 and green < 70 and blue < 70
        assert max(image.convert("RGB").getpixel((540, 60))) < 90  # Outside it the blurred source remains.
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
    complete(task, {**upload.json(), "layout": report["layout"], "overlays": report["overlays"]})
    pipeline.advance()

    task = claim("quality")
    from boti.quality import check_rendered

    expected = task["payload"]["metadata"]["segments"]
    monkeypatch.setattr("boti.ai.transcribe", lambda *args, **kwargs: {"segments": expected})
    result = check_rendered(
        ai, output, task["payload"], tmp_path, SimpleNamespace(whisper_model="fixture", whisper_device="cpu")
    )
    assert result["technical_passed"], result["technical"]
    assert '"max_seconds":15' in prompts[-1]
    complete(task, result)
    pipeline.advance()
    clip = store.one("SELECT * FROM clips WHERE job_id=?", (job_id,))
    assert clip["status"] == "ready" and publish_allowed(store, clip)
    export = store.config.root / "exports" / "pc-1-g1" / clip["id"]
    assert (export / "caption.txt").read_text() == "A moment\n\n@streamer\n\n#ad\n\n#KickClips #funny"
    shown = client.get("/api/clips", headers=admin_headers).json()["clips"][0]
    assert shown["compliance"] == {"passed": True, "issues": []}


def test_hook_and_watermark_text_cannot_inject_ass_commands(tmp_path):
    target = tmp_path / "captions.ass"
    overlays = {"watermark_text": r"{\fs200}@me", "hook": r"{\pos(1,1)}A very long hook title " * 6}
    captions_ass([], target, 20, overlays=overlays)
    mark, hook = target.read_text(encoding="utf-8-sig").split("Dialogue:")[1:]
    assert mark.count("{") == 1 and r"{\an9}" in mark and "\\fs" not in mark
    assert hook.count("{") == 1 and hook.count(r"\N") == 2 and hook.rstrip().endswith("…")

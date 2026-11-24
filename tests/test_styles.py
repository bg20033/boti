import json
import shutil

import pytest

from boti.media import captions_ass, render, run
from boti.styles import EditStyle


def test_styles_are_validated_and_default_keeps_legacy_rendering():
    assert EditStyle().required_revision == 0
    assert EditStyle(caption_motion="pop").required_revision == 1
    assert EditStyle(vignette="soft").required_revision == 3
    assert EditStyle(background_dim=True).required_revision == 3
    assert "vignette" not in EditStyle(title_enabled=True).render_options()
    with pytest.raises(ValueError):
        EditStyle(caption_color="yellow;movie=private-file")
    with pytest.raises(ValueError):
        EditStyle(caption_motion="flash")
    with pytest.raises(ValueError):
        EditStyle(vignette="soft;movie=private-file")


def test_animated_captions_keep_word_timing_and_campaign_caption_switch(tmp_path):
    words = [{"start": 0.2, "end": 0.8, "word": "Hello"}, {"start": 1.1, "end": 1.8, "word": "world!"}]
    path = tmp_path / "captions.ass"
    editing = EditStyle(
        caption_color="lime",
        caption_size="large",
        caption_position="middle",
        caption_motion="pop",
        progress_bar=True,
    )
    captions_ass([{"words": words}], path, 6, "highlight", editing=editing.render_options())
    text = path.read_text(encoding="utf-8-sig")
    assert "Default,Arial,70," in text and ",95,95,650,1" in text
    assert r"\c&H99EFD5&" in text and r"\t(0,100,\fscx100\fscy100)" in text
    assert ",0:00:00.20,0:00:00.80," in text and ",0:00:01.10,0:00:01.80," in text
    captions_ass([{"words": words}], path, 6, "highlight", {"captions": False}, editing.render_options())
    text = path.read_text(encoding="utf-8-sig")
    assert "Hello" not in text and "world" not in text


def test_pro_title_keeps_full_headline_in_three_lines(tmp_path):
    path = tmp_path / "captions.ass"
    title = "He calls it a dead buy. Then he says he doesn't know what to say."
    style = EditStyle(title_enabled=True, title_appearance="outline", vignette="soft")
    captions_ass([], path, 20, overlays={"hook": title}, editing=style.render_options())
    hook = next(line for line in path.read_text(encoding="utf-8-sig").splitlines() if "Hook,," in line)
    assert title in hook.replace(r"\N", " ")
    assert hook.count(r"\N") <= 2


@pytest.fixture
def style_queue(store):
    settings = store.settings()
    settings.analysis_worker_id = "pc-1"
    settings.editing = EditStyle(caption_color="yellow", caption_motion="pop")
    store.save_settings(settings)
    roles = ["transcribe", "analyze", "render", "quality", "repair"]
    store.heartbeat("pc-1", roles, {"render_revision": 1})
    store.heartbeat("pc-2", roles, {})
    job, _ = store.create_job("https://twitch.tv/videos/123", "123", ["pc-1-g1"])
    store.execute("UPDATE jobs SET status='processing' WHERE id=?", (job,))
    return store, job


def test_new_effects_fall_back_to_upgraded_main_while_legacy_work_stays_remote(style_queue):
    store, job = style_queue
    legacy = store.add_task(job, "render", "old", {})
    modern = store.add_task(job, "render", "new", {"render_revision": 1})
    assert store.claim("pc-2")["id"] == legacy
    assert store.claim("pc-1")["id"] == modern


def test_upgrading_remote_restores_render_offload(style_queue):
    store, job = style_queue
    store.heartbeat("pc-2", ["render"], {"render_revision": 1})
    modern = store.add_task(job, "render", "new", {"render_revision": 1})
    assert store.claim("pc-1") is None
    assert store.claim("pc-2")["id"] == modern


def test_unrecognised_render_revision_stays_queued(style_queue):
    store, job = style_queue
    store.add_task(job, "render", "future", {"render_revision": 99})
    assert store.claim("pc-1") is None and store.claim("pc-2") is None


def test_soft_finish_waits_for_capable_worker_even_without_explicit_revision(style_queue):
    store, job = style_queue
    store.heartbeat("pc-2", ["render"], {"render_revision": 2})
    task = store.add_task(job, "render", "soft", {"editing": {"vignette": "soft"}})
    assert store.claim("pc-1") is None and store.claim("pc-2") is None
    store.heartbeat("pc-2", ["render"], {"render_revision": 3})
    assert store.claim("pc-2")["id"] == task


def test_new_render_requires_worker_to_report_requested_style(style_queue, client, worker_headers):
    store, job = style_queue
    # worker_headers refreshes legacy hardware; advertise the upgraded renderer again.
    store.heartbeat("pc-2", ["render"], {"render_revision": 1})
    style = EditStyle(caption_color="yellow", caption_motion="pop").render_options()
    task_id = store.add_task(job, "render", "new", {"render_revision": 1, "editing": style})
    task = store.claim("pc-2")
    path = store.config.root / "media" / "uploaded.mp4"
    path.write_bytes(b"verified upload fixture")
    asset = store.add_asset(job, path, task_id)
    result = {"asset_id": asset["id"]}
    url = f"/api/worker/tasks/{task_id}/complete"
    body = {"lease_token": task["lease_token"], "result": result}
    assert client.post(url, headers=worker_headers["pc-2"], json=body).status_code == 422
    result["editing"] = style
    assert client.post(url, headers=worker_headers["pc-2"], json=body).status_code == 200
    assert (
        json.loads(store.one("SELECT result FROM tasks WHERE id=?", (task_id,))["result"])["editing"] == style
    )


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg not installed")
def test_actual_render_has_animated_progress_and_decodable_captions(tmp_path):
    from PIL import Image

    source = tmp_path / "source.mp4"
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x243d61:s=320x180:r=12",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(source),
        ]
    )
    style = EditStyle(
        caption_color="yellow", caption_motion="pop", color_enhance=True, progress_bar=True
    ).render_options()
    payload = {
        "start": 0,
        "end": 6,
        "caption_style": "highlight",
        "editing": style,
        "rules": {"min_seconds": 5, "max_seconds": 20},
        "segments": [
            {
                "words": [
                    {"start": 0.2, "end": 1.8, "word": "Moment"},
                    {"start": 2, "end": 4.5, "word": "test."},
                ]
            }
        ],
    }
    destination = tmp_path / "output.mp4"
    result = render(source, destination, payload)
    assert result["editing"] == style
    assert result["overlays"]["captions"] is True
    pixels = []
    for second in (1, 5.5):
        frame = tmp_path / f"frame-{second}.png"
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-ss",
                str(second),
                "-i",
                str(destination),
                "-frames:v",
                "1",
                str(frame),
            ]
        )
        with Image.open(frame).convert("RGB") as image:
            pixels.append((image.getpixel((100, 1907)), image.getpixel((900, 1907))))

    def yellow(pixel):
        return pixel[0] > 180 and pixel[1] > 160 and pixel[2] < 100

    assert yellow(pixels[0][0]) and not yellow(pixels[0][1])
    assert yellow(pixels[1][1])

import shutil

import pytest
from PIL import Image

from boti.media import probe, render, run
from boti.styles import EditStyle


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg not installed")
def test_actual_soft_finish_preserves_center_and_burns_captions_above_vignette(tmp_path):
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
            "color=c=0x909090:s=320x180:r=60",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(source),
        ]
    )
    images = []
    for name, editing in [
        ("plain", EditStyle()),
        ("soft", EditStyle(vignette="soft", background_dim=True)),
    ]:
        folder = tmp_path / name
        folder.mkdir()
        output = folder / "output.mp4"
        result = render(
            source,
            output,
            {
                "start": 0,
                "end": 3,
                "caption_style": "plain",
                "editing": editing.render_options(),
                "rules": {"min_seconds": 1, "max_seconds": 5},
                "segments": [{"start": 0.5, "end": 2.5, "text": "A clear moment"}],
            },
        )
        assert result["editing"] == editing.render_options()
        video = next(stream for stream in probe(output)["streams"] if stream["codec_type"] == "video")
        assert video["avg_frame_rate"] == "30/1"
        frame = folder / "frame.png"
        run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", str(output), "-frames:v", "1", str(frame)])
        with Image.open(frame) as image:
            images.append(image.convert("RGB"))
    before, after = images

    def brightness(point, image):
        return sum(image.getpixel(point)) / 3

    assert abs(brightness((540, 900), before) - brightness((540, 900), after)) < 5
    ratio = brightness((24, 24), after) / brightness((24, 24), before)
    assert 0.65 < ratio < 0.95  # Subtle shading, never an opaque frame or letterbox.
    area = after.crop((100, 1400, 980, 1700))
    assert (
        max(min(area.getpixel((x, y))) for x in range(0, area.width, 4) for y in range(0, area.height, 4))
        > 235
    )

from __future__ import annotations

import array
import hashlib
import json
import math
import re
import shutil
import subprocess
import wave
from fractions import Fraction
from pathlib import Path

from boti.editing import caption_lines, caption_phrases, clean_text, text_width, wrap_title


def run(args: list[str], *, cwd: Path | None = None, timeout=3600) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name}: {result.stderr.decode(errors='replace')[-1800:]}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe(path: Path) -> dict:
    return json.loads(
        run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], timeout=60
        ).stdout
    )


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def validate_output(
    path: Path, expected: float | None = None, min_length: float = 20, max_length: float = 60
) -> dict:
    info = probe(path)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if not video or not audio:
        raise ValueError("Videoja duhet të ketë pamje dhe audio")
    if (video["width"], video["height"], video["codec_name"], audio["codec_name"]) != (
        1080,
        1920,
        "h264",
        "aac",
    ):
        raise ValueError("Formati duhet të jetë 1080×1920 H.264/AAC")
    length = float(info["format"]["duration"])
    try:
        fps = float(Fraction(video.get("avg_frame_rate") or "0"))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("Videoja nuk ka framerate të vlefshëm") from exc
    if not 23 <= fps <= 60 or video.get("pix_fmt") != "yuv420p":
        raise ValueError("Videoja duhet të jetë 23–60 FPS me pikselë yuv420p")
    if int(audio.get("sample_rate", 0)) > 48000 or audio.get("channels", 0) not in (1, 2):
        raise ValueError("Audioja duhet të jetë mono/stereo, deri në 48 kHz")
    if not min_length - 0.2 <= length <= max_length + 0.5 or (
        expected is not None and abs(length - expected) > 0.6
    ):
        raise ValueError("Kohëzgjatja e rezultatit nuk përputhet me segmentin")
    run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"], timeout=300)
    return {"duration": length, "width": 1080, "height": 1920, "size": path.stat().st_size}


def extract_audio(source: Path, destination: Path):
    temp = destination.with_name(destination.stem + ".part.wav")
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(temp),
        ]
    )
    temp.replace(destination)


def audio_slice(source: Path, destination: Path, start: float, length: float):
    temp = destination.with_name(destination.stem + ".part.wav")
    with wave.open(str(source), "rb") as src, wave.open(str(temp), "wb") as dst:
        dst.setparams(src.getparams())
        src.setpos(min(src.getnframes(), int(start * src.getframerate())))
        dst.writeframes(src.readframes(int(length * src.getframerate())))
    temp.replace(destination)


def make_proxy(source: Path, destination: Path, start: float, length: float):
    temp = destination.with_name(destination.stem + ".part.mp4")
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(start),
            "-i",
            str(source),
            "-t",
            str(length),
            "-vf",
            "scale=640:-2,fps=12",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-movflags",
            "+faststart",
            str(temp),
        ]
    )
    temp.replace(destination)


def cut_segment(source: Path, destination: Path, start: float, length: float):
    # Accurate decoding at the boundary preserves the hook. Only this selected segment crosses the LAN.
    temp = destination.with_name(destination.stem + ".part.mp4")
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(start),
            "-i",
            str(source),
            "-t",
            str(length),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(temp),
        ]
    )
    temp.replace(destination)


def energy_peaks(audio: Path) -> list[dict]:
    peaks = []
    with wave.open(str(audio), "rb") as handle:
        if handle.getsampwidth() != 2 or handle.getnchannels() != 1:
            return []
        second = 0
        while data := handle.readframes(handle.getframerate()):
            values = array.array("h", data)
            rms = math.sqrt(sum(x * x for x in values) / max(1, len(values))) / 32768
            peaks.append({"second": second, "energy": round(rms, 4)})
            second += 1
    return sorted(sorted(peaks, key=lambda p: p["energy"], reverse=True)[:12], key=lambda p: p["second"])


def scene_changes(video: Path) -> list[float]:
    result = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            str(video),
            "-vf",
            "select='gt(scene,0.25)',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        timeout=600,
    )
    return [
        float(value) for value in re.findall(r"pts_time:([\d.]+)", result.stderr.decode(errors="replace"))
    ][:80]


def storyboard(video: Path, destination: Path, start: float, end: float, count=6, portrait=False):
    from PIL import Image, ImageDraw

    width, height = (360, 640) if portrait else (320, 180)
    canvas = Image.new("RGB", (width * 3, (height + 24) * ((count + 2) // 3)), "#132a4a")
    draw = ImageDraw.Draw(canvas)
    for index in range(count):
        second = start + (end - start) * (index + 0.5) / count
        frame = destination.with_name(f"frame-{index}.jpg")
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(second),
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
                str(frame),
            ],
            timeout=60,
        )
        x, y = (index % 3) * width, (index // 3) * (height + 24)
        with Image.open(frame) as image:
            canvas.paste(image, (x, y))
        draw.text((x + 8, y + height + 4), f"{second:.1f}s", fill="white")
        frame.unlink(missing_ok=True)
    canvas.save(destination, quality=85)


def _ass_time(seconds: float) -> str:
    cents = max(0, round(seconds * 100))
    return f"{cents // 360000}:{cents // 6000 % 60:02}:{cents // 100 % 60:02}.{cents % 100:02}"


# Safe areas on a 1080×1920 canvas: platform buttons cover the far right and the bottom fifth.
_ANCHOR = {"top_left": 7, "top": 8, "top_right": 9, "bottom_left": 1, "bottom": 2, "bottom_right": 3}
_MARK_TOP, _MARK_BOTTOM, _MARK_SIDE = 150, 230, 60


def captions_ass(segments: list[dict], destination: Path, length: float, style="plain", overlays=None):
    """Spoken captions plus the campaign's burned-in text: watermark for the whole clip, hook at the start."""
    overlays = overlays or {}
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,62,&H00FFFFFF,&H0000FFFF,&H00131620,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,95,95,340,1
Style: Mark,Arial,38,&H30FFFFFF,&H30FFFFFF,&H60000000,&H80000000,-1,0,0,0,100,100,1,0,1,2,0,9,60,60,150,1
Style: Hook,Arial,70,&H00FFFFFF,&H00FFFFFF,&H00131620,&HA0000000,-1,0,0,0,100,100,0,0,3,14,0,8,70,70,270,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    mark = clean_text(overlays.get("watermark_text") or "")
    if mark:
        position = overlays.get("watermark_position") or "top_right"
        margin = _MARK_TOP if position.startswith("top") else _MARK_BOTTOM
        lines.append(
            f"Dialogue: 1,{_ass_time(0)},{_ass_time(length)},Mark,,{_MARK_SIDE},{_MARK_SIDE},{margin},,"
            rf"{{\an{_ANCHOR.get(position, 9)}}}{mark}" + "\n"
        )
    hook = wrap_title(overlays.get("hook") or "")
    if hook:
        lines.append(
            f"Dialogue: 2,{_ass_time(0)},{_ass_time(min(3.5, length))},Hook,,0,0,0,,"
            + r"{\fad(0,250)}"
            + r"\N".join(hook)
            + "\n"
        )
    for group in caption_phrases(segments, length) if overlays.get("captions", True) else []:
        rows = caption_lines(group)
        longest = max(text_width(" ".join(group[i]["word"] for i in row)) for row in rows)
        font = min(62, max(12, int(62 * 26 / max(26, longest))))

        def emit(start, end, active=None):
            if _ass_time(start) == _ass_time(end):
                return
            text = r"\N".join(
                " ".join(
                    (r"{\c&H66E8FF&}" + group[i]["word"] + r"{\c&HFFFFFF&}")
                    if i == active
                    else group[i]["word"]
                    for i in row
                )
                for row in rows
            )
            if font != 62:
                text = rf"{{\fs{font}}}" + text
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}\n")

        if style == "highlight" and all(word["timed"] for word in group):
            cursor = group[0]["start"]
            for index, word in enumerate(group):
                if word["start"] > cursor:
                    emit(cursor, word["start"])
                emit(word["start"], word["end"], index)
                cursor = word["end"]
        else:
            emit(group[0]["start"], group[-1]["end"])
    destination.write_text("".join(lines), encoding="utf-8-sig")


def logo_overlay(position: str) -> str:
    x = {"left": str(_MARK_SIDE), "right": f"W-w-{_MARK_SIDE}"}.get(position.rsplit("_", 1)[-1], "(W-w)/2")
    y = str(_MARK_TOP) if position.startswith("top") else f"H-h-{_MARK_BOTTOM}"
    return f"overlay={x}:{y}"


def loudness_filter(source: Path, length: float) -> tuple[str, dict]:
    """Measure first, normalize second. Preserve dynamics when linear gain is feasible."""
    target = "loudnorm=I=-16:TP=-1.5:LRA=11"
    measurement = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(source),
            "-t",
            str(length),
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            target + ":print_format=json",
            "-f",
            "null",
            "-",
        ],
        timeout=300,
    )
    matches = re.findall(r'\{\s*"input_i".*?\}', measurement.stderr.decode(errors="replace"), re.S)
    if not matches:
        raise ValueError("FFmpeg nuk ktheu matjen e zërit")
    measured = json.loads(matches[-1])
    values = {
        key: float(measured[key])
        for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    }
    if not all(math.isfinite(value) for value in values.values()):
        # Silence has -inf LUFS. Never pass non-finite values into a filter expression.
        return "anull", {"mode": "silent", "input_lufs": None}
    settings = ":".join(
        f"{name}={values[key]:.4f}"
        for name, key in (
            ("measured_I", "input_i"),
            ("measured_TP", "input_tp"),
            ("measured_LRA", "input_lra"),
            ("measured_thresh", "input_thresh"),
            ("offset", "target_offset"),
        )
    )
    linear = values["input_lra"] <= 11 and values["input_tp"] + (-16 - values["input_i"]) <= -1.5
    return target + ":" + settings + ":linear=true", {
        "mode": "two_pass_linear" if linear else "two_pass_dynamic",
        "input_lufs": values["input_i"],
        "input_peak_db": values["input_tp"],
        "target_lufs": -16,
        "target_peak_db": -1.5,
    }


def face_center(video: Path) -> float | None:
    try:
        import cv2
    except ImportError:
        return None
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    capture = cv2.VideoCapture(str(video))
    try:
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        hits = []
        for portion in (0.08, 0.25, 0.42, 0.58, 0.75, 0.92):
            capture.set(cv2.CAP_PROP_POS_FRAMES, frames * portion)
            ok, frame = capture.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            small = cv2.resize(frame, (640, max(1, round(height * 640 / width))))
            faces = detector.detectMultiScale(
                cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), scaleFactor=1.1, minNeighbors=7, minSize=(45, 45)
            )
            if len(faces) == 1:
                x, _, w, _ = faces[0]
                hits.append(float((x + w / 2) / 640))
        if len(hits) >= 5 and max(hits) - min(hits) <= 0.15:
            return sorted(hits)[len(hits) // 2]
        return None
    finally:
        capture.release()


def available_encoder(preference="auto") -> str:
    if preference == "cpu":
        return "libx264"
    try:
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=s=128x128:d=0.1",
                "-c:v",
                "h264_nvenc",
                "-f",
                "null",
                "-",
            ],
            timeout=20,
        )
        return "h264_nvenc"
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired):
        return "libx264"


def render(
    source: Path, destination: Path, payload: dict, encoder="libx264", logo: Path | None = None
) -> dict:
    work = destination.parent
    length = payload["end"] - payload["start"]
    subtitles = work / "captions.ass"
    rules = payload.get("rules") or {}
    overlays = {
        "captions": rules.get("captions", True),
        "watermark_text": rules.get("watermark_text", ""),
        "watermark_position": rules.get("watermark_position", "top_right"),
        "hook": payload.get("title", "") if rules.get("hook_title") else "",
    }
    if rules.get("logo_sha256") and (logo is None or sha256(logo) != rules["logo_sha256"]):
        # Never deliver a campaign clip silently missing its required logo.
        raise ValueError("Logoja e fushatës mungon ose nuk përputhet me rregullat")
    captions_ass(
        payload.get("segments", []), subtitles, length, payload.get("caption_style", "highlight"), overlays
    )
    audio_filter, audio_report = loudness_filter(source, length)
    info = probe(source)
    stream = next(s for s in info["streams"] if s["codec_type"] == "video")
    width, height = stream["width"], stream["height"]
    profile = payload.get("profile", {})
    mode = "fit" if payload.get("force_fit") else profile.get("mode", "auto")
    if mode == "auto":
        mode = payload.get("content_type", "mixed")
    camera = profile.get("camera")
    center = face_center(source) if mode == "talk" else None
    layout = "fit"
    if mode == "gaming" and camera:
        from boti.models import Box

        box = Box.model_validate(camera)
        crop_w, crop_h = max(2, int(width * box.w) // 2 * 2), max(2, int(height * box.h) // 2 * 2)
        crop_x = min(int(width * box.x) // 2 * 2, (width - crop_w) // 2 * 2)
        crop_y = min(int(height * box.y) // 2 * 2, (height - crop_h) // 2 * 2)
        graph = (
            f"[0:v]split=2[g][c];[g]scale=1080:1320:force_original_aspect_ratio=decrease,"
            f"pad=1080:1320:(ow-iw)/2:(oh-ih)/2:color=0x132a4a[g1];"
            f"[c]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
            "scale=1080:600:force_original_aspect_ratio=decrease,"
            "pad=1080:600:(ow-iw)/2:(oh-ih)/2:color=0x132a4a[c1];[c1][g1]vstack=inputs=2[layout];"
        )
        layout = "camera_game"
    elif center is not None and width / height > 9 / 16:
        crop_width = int(height * 9 / 16) // 2 * 2
        left = int(max(0, min(width - crop_width, center * width - crop_width / 2))) // 2 * 2
        graph = f"[0:v]crop={crop_width}:{height}:{left}:0,scale=1080:1920[layout];"
        layout = "speaker"
    else:
        graph = (
            "[0:v]split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=24:2[back];"
            "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[front];"
            "[back][front]overlay=(W-w)/2:(H-h)/2[layout];"
        )
    if rules.get("logo_sha256"):
        # Bounded size keeps the logo out of the caption area whatever its aspect ratio.
        graph += (
            "[1:v]scale=260:150:force_original_aspect_ratio=decrease,format=rgba,"
            "colorchannelmixer=aa=0.92[logo];"
            "[layout]setsar=1,fps=30,subtitles=filename=captions.ass[text];"
            f"[text][logo]{logo_overlay(rules.get('logo_position', 'top_left'))},format=yuv420p[out]"
        )
    else:
        graph += "[layout]setsar=1,fps=30,subtitles=filename=captions.ass,format=yuv420p[out]"
    video_options = (
        ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "20", "-b:v", "0"]
        if encoder == "h264_nvenc"
        else ["-c:v", "libx264", "-preset", "fast", "-crf", "20"]
    )
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        *(["-i", str(logo)] if rules.get("logo_sha256") else []),
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-map",
        "0:a:0",
        "-t",
        str(length),
        *video_options,
        "-maxrate",
        "20M",
        "-bufsize",
        "40M",
        "-g",
        "60",
        "-af",
        audio_filter,
        "-ar",
        "48000",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    try:
        run(command, cwd=work)
    except RuntimeError:
        if encoder == "h264_nvenc":
            return render(source, destination, payload, "libx264", logo)
        raise
    return {
        **validate_output(destination, length, rules.get("min_seconds", 20), rules.get("max_seconds", 60)),
        "layout": layout,
        "encoder": encoder,
        "audio": audio_report,
        # Evidence for the compliance check: exactly which campaign overlays are burned into these bytes.
        "overlays": {
            "captions": bool(overlays["captions"]),
            "watermark": overlays["watermark_text"],
            "logo": rules.get("logo_sha256", ""),
            "hook": bool(overlays["hook"]),
        },
    }


def dependencies() -> dict:
    tools = {name: bool(shutil.which(name)) for name in ("ffmpeg", "ffprobe")}
    if tools["ffmpeg"]:
        try:
            filters = run(["ffmpeg", "-hide_banner", "-filters"], timeout=20).stdout.decode()
            tools["subtitles"] = "subtitles" in filters
        except RuntimeError:
            tools["subtitles"] = False
    return tools

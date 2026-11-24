"""Estimate complete source storage before downloading a long VOD."""

import math


def source_bytes(info):
    for key in ("filesize", "filesize_approx"):
        value = info.get(key)
        if type(value) in (int, float) and math.isfinite(value) and value > 0:
            return int(value)
    duration, bitrate = info.get("duration"), info.get("tbr")
    if all(type(n) in (int, float) and math.isfinite(n) and n > 0 for n in (duration, bitrate)):
        return int(duration * bitrate * 1000 / 8)
    return 0


def ingest_reserve(metadata, directory):
    estimate = metadata.get("download_estimated_bytes", 0)
    downloaded = 0
    if estimate:
        for path in directory.glob("source.*"):
            try:
                if (
                    path.is_file()
                    and "-Frag" not in path.name
                    and path.suffix in {".mp4", ".webm", ".mkv", ".part"}
                ):
                    downloaded = max(downloaded, path.stat().st_size)
            except OSError:
                pass  # A completed download can be renamed while the live snapshot is being read.
    return metadata.get("reserve_bytes", 0) + max(0, estimate - downloaded)

"""Browser-playable preview of the source video.

iPhones record HEVC by default ("High Efficiency"), and Chromium/Firefox will
not play it. The analysis does not care (OpenCV decodes HEVC fine), but the
web UI needs a <video> that works, so for anything that is not H.264 we make
a 720p H.264 copy once. H.264 sources are served as-is.
"""

from __future__ import annotations

from pathlib import Path

import cv2

from .ffmpeg import run

BROWSER_SAFE = {"avc1", "h264", "H264", "AVC1"}


def source_fourcc(path: str | Path) -> str:
    cap = cv2.VideoCapture(str(path))
    try:
        code = int(cap.get(cv2.CAP_PROP_FOURCC))
    finally:
        cap.release()
    return "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00")


def needs_preview(path: str | Path) -> bool:
    try:
        return source_fourcc(path) not in BROWSER_SAFE
    except Exception:
        return True


def make_preview(src: str | Path, dst: str | Path, *, height: int = 720) -> Path:
    dst = Path(dst)
    run([
        "-i", str(src),
        "-vf", f"scale=-2:{height}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart", "-y", str(dst),
    ])
    return dst

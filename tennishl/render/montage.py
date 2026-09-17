"""Join clips into one highlight reel.

All clips are encoded with identical parameters by clips.py, so the concat
demuxer can stitch them without re-encoding. That makes re-rendering after a
user toggles a clip on/off nearly instant.
"""

from __future__ import annotations

from pathlib import Path

from ..video.ffmpeg import run


def _escape_concat_path(p: Path) -> str:
    # The concat demuxer wants single quotes escaped as '\''.
    return str(p.resolve()).replace("'", r"'\''")


def build_montage(clip_paths: list[Path], out_path: Path) -> Path:
    if not clip_paths:
        raise ValueError("No clips to build a montage from")

    list_path = out_path.with_suffix(".concat.txt")
    lines = [f"file '{_escape_concat_path(p)}'" for p in clip_paths]
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    run([
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        "-movflags", "+faststart",
        "-y", str(out_path),
    ])
    list_path.unlink(missing_ok=True)
    return out_path

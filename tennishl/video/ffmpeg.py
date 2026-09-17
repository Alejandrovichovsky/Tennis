"""Thin ffmpeg wrapper.

We shell out to ffmpeg for anything that touches audio or container muxing,
and use OpenCV for pixel work. That split keeps us out of the business of
re-implementing A/V sync.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence


class FFmpegError(RuntimeError):
    pass


_CACHED_EXE: str | None = None


def ffmpeg_exe() -> str:
    """Locate an ffmpeg binary.

    Order: TENNISHL_FFMPEG env var, system ffmpeg, then the static binary that
    ships with imageio-ffmpeg (so ``pip install -r requirements.txt`` is enough
    on a clean machine).
    """
    global _CACHED_EXE
    if _CACHED_EXE:
        return _CACHED_EXE

    env = os.environ.get("TENNISHL_FFMPEG")
    if env and Path(env).exists():
        _CACHED_EXE = env
        return _CACHED_EXE

    found = shutil.which("ffmpeg")
    if found:
        _CACHED_EXE = found
        return _CACHED_EXE

    try:
        import imageio_ffmpeg

        _CACHED_EXE = imageio_ffmpeg.get_ffmpeg_exe()
        return _CACHED_EXE
    except Exception as exc:  # pragma: no cover - only on a broken install
        raise FFmpegError(
            "No ffmpeg found. Install it, or `pip install imageio-ffmpeg`, "
            "or set TENNISHL_FFMPEG=/path/to/ffmpeg"
        ) from exc


def run(args: Sequence[str], *, stdin_data: bytes | None = None) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", *args]
    proc = subprocess.run(
        cmd,
        input=stdin_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise FFmpegError(
            f"ffmpeg failed ({proc.returncode})\n"
            f"cmd: {' '.join(cmd[:14])} ...\n"
            f"stderr: {proc.stderr.decode('utf-8', 'replace')[-2000:]}"
        )
    return proc


def has_audio_stream(path: str | Path) -> bool:
    """ffprobe is not always installed; ask ffmpeg itself instead."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-i", str(path)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    text = proc.stderr.decode("utf-8", "replace")
    return "Audio:" in text

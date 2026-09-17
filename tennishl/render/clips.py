"""Cutting clips out of the source video.

Two paths:

* **Plain cut** - one ffmpeg call, re-encoded so the cut is frame-accurate
  (stream-copy would snap to the nearest keyframe, i.e. up to several seconds
  off on phone footage). Audio is kept.

* **Cut with ball trail** - OpenCV decodes the range at full resolution, we
  draw the trail, pipe raw frames into ffmpeg, then mux the original audio
  back in. Slower, so only used where the ball track is trusted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

from ..config import Config
from ..types import BallTrack, Highlight, VideoInfo
from ..video.ffmpeg import ffmpeg_exe, has_audio_stream, run


def _fade_filter(duration_s: float, fade_s: float) -> str | None:
    if fade_s <= 0 or duration_s <= 2 * fade_s + 0.1:
        return None
    out_start = max(0.0, duration_s - fade_s)
    return f"fade=t=in:st=0:d={fade_s:.3f},fade=t=out:st={out_start:.3f}:d={fade_s:.3f}"


def _audio_fade_filter(duration_s: float, fade_s: float) -> str | None:
    if fade_s <= 0 or duration_s <= 2 * fade_s + 0.1:
        return None
    out_start = max(0.0, duration_s - fade_s)
    return f"afade=t=in:st=0:d={fade_s:.3f},afade=t=out:st={out_start:.3f}:d={fade_s:.3f}"


def cut_plain(
    info: VideoInfo,
    start_s: float,
    end_s: float,
    out_path: Path,
    cfg: Config,
    *,
    with_audio: bool | None = None,
) -> Path:
    duration = max(0.1, end_s - start_s)
    if with_audio is None:
        with_audio = has_audio_stream(info.path)

    args = [
        "-ss", f"{start_s:.3f}",
        "-i", info.path,
        "-t", f"{duration:.3f}",
        "-map", "0:v:0",
    ]
    vf = _fade_filter(duration, cfg.clip.fade_s)
    if vf:
        args += ["-vf", vf]
    args += [
        "-c:v", "libx264",
        "-preset", cfg.clip.preset,
        "-crf", str(cfg.clip.crf),
        "-pix_fmt", "yuv420p",
        "-r", f"{info.fps:.4f}",
    ]
    if with_audio:
        args += ["-map", "0:a:0?"]
        af = _audio_fade_filter(duration, cfg.clip.fade_s)
        if af:
            args += ["-af", af]
        args += ["-c:a", "aac", "-b:a", cfg.clip.audio_bitrate]
    else:
        args += ["-an"]
    args += ["-movflags", "+faststart", "-y", str(out_path)]
    run(args)
    return out_path


def _draw_trail(
    frame: np.ndarray,
    track: BallTrack,
    t: float,
    trail_seconds: float,
    scale_from_proxy: float,
) -> None:
    """Draw the last ``trail_seconds`` of the ball path onto ``frame`` in place."""
    h, w = frame.shape[:2]
    pts = [p for p in track.points if t - trail_seconds <= p.t <= t + 1e-3]
    if len(pts) < 2:
        return

    n = len(pts)
    prev = None
    for i, p in enumerate(pts):
        x, y = int(p.x * w), int(p.y * h)
        age = (i + 1) / n  # 0 = oldest, 1 = newest
        thickness = max(1, int(round(2 + 4 * age)))
        colour = (60, 240, 255)  # BGR: tennis-ball yellow
        if prev is not None:
            cv2.line(frame, prev, (x, y), colour, thickness, cv2.LINE_AA)
        prev = (x, y)

    head = pts[-1]
    r = max(4, int(round(head.radius_px * scale_from_proxy * 1.4)))
    cx, cy = int(head.x * w), int(head.y * h)
    cv2.circle(frame, (cx, cy), r, (60, 240, 255), 2, cv2.LINE_AA)


def cut_with_trail(
    info: VideoInfo,
    start_s: float,
    end_s: float,
    track: BallTrack,
    out_path: Path,
    cfg: Config,
    *,
    ball_proxy_width: int,
) -> Path:
    duration = max(0.1, end_s - start_s)
    tmp_video = out_path.with_suffix(".video.mp4")

    cap = cv2.VideoCapture(info.path)
    if not cap.isOpened():
        raise IOError(f"Could not open video: {info.path}")

    first = int(round(start_s * info.fps))
    last = int(round(end_s * info.fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, first)

    args = [
        ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{info.width}x{info.height}",
        "-r", f"{info.fps:.4f}",
        "-i", "-",
    ]
    vf = _fade_filter(duration, cfg.clip.fade_s)
    if vf:
        args += ["-vf", vf]
    args += [
        "-c:v", "libx264", "-preset", cfg.clip.preset, "-crf", str(cfg.clip.crf),
        "-pix_fmt", "yuv420p", "-y", str(tmp_video),
    ]
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None

    scale_from_proxy = info.width / float(max(1, min(ball_proxy_width, info.width)))
    frame_index = first
    try:
        while frame_index <= last:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            _draw_trail(frame, track, frame_index / info.fps, cfg.ball.trail_seconds, scale_from_proxy)
            proc.stdin.write(frame.tobytes())
            frame_index += 1
    finally:
        cap.release()
        try:
            proc.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
        err = proc.stderr.read() if proc.stderr else b""
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg trail encode failed: {err.decode('utf-8', 'replace')[-1500:]}")

    # Mux original audio back in (if any).
    if has_audio_stream(info.path):
        args = [
            "-i", str(tmp_video),
            "-ss", f"{start_s:.3f}", "-t", f"{duration:.3f}", "-i", info.path,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "copy",
        ]
        af = _audio_fade_filter(duration, cfg.clip.fade_s)
        if af:
            args += ["-af", af]
        args += ["-c:a", "aac", "-b:a", cfg.clip.audio_bitrate, "-shortest",
                 "-movflags", "+faststart", "-y", str(out_path)]
        run(args)
        tmp_video.unlink(missing_ok=True)
    else:
        tmp_video.replace(out_path)
    return out_path


def cut_highlight(
    info: VideoInfo,
    h: Highlight,
    out_dir: Path,
    cfg: Config,
    *,
    ball_proxy_width: int,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{h.rank:02d}_{h.id}_{int(h.clip_start_s):05d}s.mp4"
    out_path = out_dir / name
    use_trail = (
        cfg.clip.draw_ball_trail
        and h.ball_track is not None
        and h.ball_track.confidence >= cfg.ball.min_confidence
    )
    if use_trail:
        assert h.ball_track is not None
        cut_with_trail(
            info, h.clip_start_s, h.clip_end_s, h.ball_track, out_path, cfg,
            ball_proxy_width=ball_proxy_width,
        )
    else:
        cut_plain(info, h.clip_start_s, h.clip_end_s, out_path, cfg)
    h.clip_path = str(out_path)
    return out_path

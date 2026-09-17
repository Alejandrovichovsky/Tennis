"""Proxy frame reading.

Rule of the project: analysis never runs at 1080p. Every consumer asks for a
target width and a target sample rate, and gets downscaled frames.

The important trick here is ``cap.grab()``: it advances the decoder without
doing colour conversion or allocating a numpy array, so skipping frames costs
a fraction of decoding them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from ..types import VideoInfo


def probe(path: str | Path) -> VideoInfo:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise IOError(f"Could not open video: {path}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()

    if width <= 0 or height <= 0:
        raise IOError(f"Video reports a zero frame size: {path}")
    # Some containers lie about frame_count; guard against nonsense.
    if count <= 0:
        count = 0
    duration = count / fps if count else 0.0
    return VideoInfo(
        path=str(path),
        width=width,
        height=height,
        fps=fps,
        frame_count=count,
        duration_s=duration,
    )


@dataclass
class ProxyFrame:
    index: int          # index within the sampled sequence
    frame_index: int    # index in the source video
    t: float            # seconds from the start of the source
    image: np.ndarray   # BGR, downscaled


def proxy_scale(info: VideoInfo, target_width: int) -> float:
    if target_width <= 0 or target_width >= info.width:
        return 1.0
    return target_width / float(info.width)


def iter_proxy_frames(
    info: VideoInfo,
    *,
    target_width: int,
    target_fps: float = 0.0,
    start_s: float = 0.0,
    end_s: float | None = None,
    grayscale: bool = False,
) -> Iterator[ProxyFrame]:
    """Yield downscaled frames between ``start_s`` and ``end_s``.

    ``target_fps <= 0`` means "every frame". Otherwise we keep roughly every
    ``fps / target_fps``-th frame; the stride is an integer so timestamps stay
    exactly on source frame boundaries (important when we later cut clips).
    """
    stride = 1
    if target_fps and target_fps > 0:
        stride = max(1, int(round(info.fps / target_fps)))

    scale = proxy_scale(info, target_width)
    out_w = max(2, int(round(info.width * scale)))
    out_h = max(2, int(round(info.height * scale)))

    cap = cv2.VideoCapture(info.path)
    if not cap.isOpened():
        raise IOError(f"Could not open video: {info.path}")

    try:
        first_frame = int(round(start_s * info.fps)) if start_s > 0 else 0
        if first_frame > 0:
            # Seeking by frame index is exact enough for our purposes and far
            # cheaper than decoding from zero on a 2h file.
            cap.set(cv2.CAP_PROP_POS_FRAMES, first_frame)

        last_frame = None
        if end_s is not None:
            last_frame = int(round(end_s * info.fps))

        frame_index = first_frame
        out_index = 0
        while True:
            if last_frame is not None and frame_index > last_frame:
                break

            want = ((frame_index - first_frame) % stride) == 0
            if not want:
                if not cap.grab():
                    break
                frame_index += 1
                continue

            ok, frame = cap.read()
            if not ok or frame is None:
                break

            if scale != 1.0:
                frame = cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_AREA)
            if grayscale:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            yield ProxyFrame(
                index=out_index,
                frame_index=frame_index,
                t=frame_index / info.fps,
                image=frame,
            )
            out_index += 1
            frame_index += 1
    finally:
        cap.release()


def estimate_sample_count(info: VideoInfo, target_fps: float) -> int:
    """Used only to drive the progress bar."""
    if info.frame_count <= 0:
        return 0
    stride = max(1, int(round(info.fps / target_fps))) if target_fps > 0 else 1
    return info.frame_count // stride

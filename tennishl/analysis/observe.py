"""Coarse pass: turn a long video into a compact stream of observations.

This is the only stage that decodes the whole file, so it has to be cheap.
For every sampled frame we record a handful of numbers and a short list of
foreground blobs - never pixels. A 2h match at 10 fps becomes ~72k small
records, which we can then re-analyse in memory as many times as we like
while tuning heuristics, without touching the decoder again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import cv2
import numpy as np

from ..config import Config
from ..types import Blob, FrameObservation, VideoInfo
from ..video.reader import estimate_sample_count, iter_proxy_frames, proxy_scale

ProgressFn = Callable[[int, int], None]


@dataclass
class CoarseResult:
    observations: list[FrameObservation]
    motion_map: np.ndarray       # float32, accumulated foreground hits
    proxy_size: tuple[int, int]  # (width, height) of the coarse proxy
    sample_fps: float


def _open_kernel() -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))


def _close_kernel() -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))


def run_coarse_pass(
    info: VideoInfo,
    cfg: Config,
    *,
    progress: Optional[ProgressFn] = None,
    max_seconds: float | None = None,
) -> CoarseResult:
    """Background-subtract the whole video at low resolution."""
    scale = proxy_scale(info, cfg.proxy.coarse_width)
    proxy_w = max(2, int(round(info.width * scale)))
    proxy_h = max(2, int(round(info.height * scale)))
    frame_area = float(proxy_w * proxy_h)

    # history in *samples*: ~12 seconds of coarse-pass frames. Long enough that
    # a player standing still gets absorbed into the background (we want motion,
    # not presence), short enough to follow the sun going behind a cloud.
    history = max(30, int(round(cfg.proxy.coarse_fps * 12)))
    subtractor = cv2.createBackgroundSubtractorMOG2(
        history=history, varThreshold=24, detectShadows=True
    )

    min_area = cfg.player.min_area_frac * frame_area
    max_area = cfg.player.max_area_frac * frame_area
    open_k = _open_kernel()
    close_k = _close_kernel()

    motion_map = np.zeros((proxy_h, proxy_w), dtype=np.float32)
    observations: list[FrameObservation] = []
    prev_gray: np.ndarray | None = None

    total = estimate_sample_count(info, cfg.proxy.coarse_fps)
    if max_seconds and info.fps:
        total = min(total, int(max_seconds * cfg.proxy.coarse_fps)) or total

    for pf in iter_proxy_frames(
        info,
        target_width=cfg.proxy.coarse_width,
        target_fps=cfg.proxy.coarse_fps,
        end_s=max_seconds,
    ):
        gray = cv2.cvtColor(pf.image, cv2.COLOR_BGR2GRAY)

        # Camera motion: if the tripod is bumped or someone picks up the phone,
        # *most* of the frame changes at once. Players alone never do that.
        if prev_gray is None:
            camera_motion = 0.0
        else:
            diff = cv2.absdiff(gray, prev_gray)
            camera_motion = float(np.count_nonzero(diff > 25)) / frame_area
        prev_gray = gray

        mask = subtractor.apply(pf.image)
        # MOG2 marks shadows as 127; keep only hard foreground.
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_k)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_k)

        motion_map += (mask > 0).astype(np.float32)

        n, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        blobs: list[Blob] = []
        for i in range(1, n):
            area = float(stats[i, cv2.CC_STAT_AREA])
            if area < min_area or area > max_area:
                continue
            w = float(stats[i, cv2.CC_STAT_WIDTH])
            h = float(stats[i, cv2.CC_STAT_HEIGHT])
            if w <= 0 or h / w < cfg.player.min_aspect:
                continue
            blobs.append(Blob(cx=float(centroids[i][0]), cy=float(centroids[i][1]), w=w, h=h, area=area))

        blobs.sort(key=lambda b: b.area, reverse=True)
        del blobs[cfg.player.max_blobs_per_frame:]

        observations.append(
            FrameObservation(
                index=pf.index,
                frame_index=pf.frame_index,
                t=pf.t,
                fg_ratio=float(np.count_nonzero(mask)) / frame_area,
                camera_motion=camera_motion,
                blobs=blobs,
            )
        )

        if progress and total:
            progress(pf.index + 1, total)

    if progress and total:
        progress(total, total)

    return CoarseResult(
        observations=observations,
        motion_map=motion_map,
        proxy_size=(proxy_w, proxy_h),
        sample_fps=cfg.proxy.coarse_fps,
    )

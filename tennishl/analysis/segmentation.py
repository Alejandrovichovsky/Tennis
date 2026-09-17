"""Rally segmentation: activity signal in, start/end timestamps out.

Pure numpy - no video, no OpenCV. That makes it unit-testable on synthetic
signals (see tests/test_segmentation.py) and is the piece that ports most
directly to Swift.

Two ideas do most of the work:

  * **Hysteresis.** One threshold makes a segment flicker on and off around
    the boundary. Two (a high one to start, a lower one to stop) give stable
    edges - the same trick a thermostat uses.

  * **Start backtracking.** The signal crosses the high threshold in the
    middle of the first shot, not at its start. So once we have triggered, we
    walk backwards to where the signal last came up through the *low*
    threshold. That is where the serve motion began.

Rejected candidates are returned too, with a reason, so a wrong cut can be
debugged instead of guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import Config
from ..types import Segment


@dataclass
class Rejection:
    start_s: float
    end_s: float
    reason: str
    detail: float

    def to_dict(self) -> dict:
        return {
            "start_s": round(self.start_s, 3),
            "end_s": round(self.end_s, 3),
            "duration_s": round(self.end_s - self.start_s, 3),
            "reason": self.reason,
            "detail": round(self.detail, 4),
        }


@dataclass
class SegmentationResult:
    segments: list[Segment]
    rejected: list[Rejection]
    enter_threshold: float
    exit_threshold: float

    def to_dict(self) -> dict:
        return {
            "enter_threshold": round(self.enter_threshold, 4),
            "exit_threshold": round(self.exit_threshold, 4),
            "segments": [s.to_dict() for s in self.segments],
            "rejected": [r.to_dict() for r in self.rejected],
        }


def compute_thresholds(activity: np.ndarray, cfg: Config) -> tuple[float, float]:
    """Self-calibrating thresholds from the video's own dynamic range."""
    if activity.size == 0:
        return 1.0, 1.0
    base = float(np.percentile(activity, 20.0))
    top = float(np.percentile(activity, 95.0))
    span = max(1e-6, top - base)
    enter = base + cfg.segmentation.enter_frac * span
    exit_ = base + cfg.segmentation.exit_frac * span
    return enter, exit_


def _raw_intervals(
    t: np.ndarray, a: np.ndarray, enter: float, exit_: float, min_gap_s: float
) -> list[tuple[int, int]]:
    n = a.size
    intervals: list[tuple[int, int]] = []
    active = False
    start_idx = 0
    below_since: int | None = None

    for i in range(n):
        if not active:
            if a[i] >= enter:
                j = i
                while j > 0 and a[j - 1] >= exit_:
                    j -= 1
                start_idx = j
                active = True
                below_since = None
        else:
            if a[i] < exit_:
                if below_since is None:
                    below_since = i
                elif t[i] - t[below_since] >= min_gap_s:
                    intervals.append((start_idx, below_since))
                    active = False
                    below_since = None
            else:
                below_since = None

    if active:
        end_idx = below_since if below_since is not None else n - 1
        intervals.append((start_idx, max(start_idx, end_idx)))
    return intervals


def _merge_close(
    intervals: list[tuple[int, int]], t: np.ndarray, merge_gap_s: float
) -> list[tuple[int, int]]:
    if not intervals:
        return []
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        ps, pe = merged[-1]
        if t[s] - t[pe] <= merge_gap_s:
            merged[-1] = (ps, e)
        else:
            merged.append((s, e))
    return merged


def segment_signal(
    t: np.ndarray,
    activity: np.ndarray,
    spread: np.ndarray,
    camera_motion: np.ndarray,
    cfg: Config,
) -> SegmentationResult:
    t = np.asarray(t, dtype=np.float64)
    activity = np.asarray(activity, dtype=np.float64)
    if t.size != activity.size:
        raise ValueError("t and activity must have the same length")
    if spread.size != t.size or camera_motion.size != t.size:
        raise ValueError("spread and camera_motion must match the signal length")

    enter, exit_ = compute_thresholds(activity, cfg)
    intervals = _raw_intervals(t, activity, enter, exit_, cfg.segmentation.min_gap_s)
    intervals = _merge_close(intervals, t, cfg.segmentation.merge_gap_s)

    segments: list[Segment] = []
    rejected: list[Rejection] = []

    for s, e in intervals:
        e = max(s, e)
        start_s = float(t[s])
        end_s = float(t[e])
        duration = end_s - start_s
        window = activity[s : e + 1]
        both = float(np.mean(spread[s : e + 1])) if e >= s else 0.0
        cam = float(np.max(camera_motion[s : e + 1])) if e >= s else 0.0

        if duration < cfg.segmentation.min_duration_s:
            rejected.append(Rejection(start_s, end_s, "too_short", duration))
            continue
        if duration > cfg.segmentation.max_duration_s:
            # Almost always warm-up hitting or a mis-merged stretch, not a
            # 50-second point. Logged so it can be inspected.
            rejected.append(Rejection(start_s, end_s, "too_long", duration))
            continue
        if both < cfg.segmentation.require_both_sides_frac:
            rejected.append(Rejection(start_s, end_s, "players_not_on_both_sides", both))
            continue
        if cam > cfg.segmentation.max_camera_motion:
            rejected.append(Rejection(start_s, end_s, "camera_moved", cam))
            continue

        segments.append(
            Segment(
                start_s=start_s,
                end_s=end_s,
                mean_activity=float(np.mean(window)) if window.size else 0.0,
                peak_activity=float(np.max(window)) if window.size else 0.0,
                both_sides_frac=both,
                camera_motion=cam,
            )
        )

    return SegmentationResult(
        segments=segments, rejected=rejected, enter_threshold=enter, exit_threshold=exit_
    )

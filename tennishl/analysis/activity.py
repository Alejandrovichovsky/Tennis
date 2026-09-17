"""Turn per-frame observations into one scalar "is there a point being played
right now" signal.

The mental model: during a rally, two players move fast, in opposite halves of
the court, changing direction all the time. Between points they walk slowly,
often together toward one baseline, or they leave the court area entirely.

Three ingredients, all cheap:
  1. ``fg_ratio``     - how much of the court area is moving at all
  2. ``player_speed`` - how fast the fastest player blob is moving
  3. ``spread``       - are there players on both sides of the net

Speed carries the most information: walking and sprinting differ by a factor
of several, while "amount of pixels moving" barely differs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import Config
from ..types import Blob, FrameObservation
from .court import CourtModel
from .signals import moving_average, robust_normalize


@dataclass
class ActivitySignal:
    t: np.ndarray            # seconds, per sample
    raw: np.ndarray          # unsmoothed combination, 0..1-ish
    smoothed: np.ndarray     # what segmentation actually thresholds
    speed: np.ndarray        # normalised player speed
    fg: np.ndarray           # normalised foreground ratio
    spread: np.ndarray       # 0/1 both-sides indicator
    camera_motion: np.ndarray
    sample_dt: float

    def __len__(self) -> int:
        return int(self.t.size)


def select_players(blobs: list[Blob], court: CourtModel) -> tuple[Blob | None, Blob | None]:
    """Pick at most one player per side of the net: the biggest blob there.

    Not a real multi-object tracker - on purpose. For a fixed camera with two
    players this is nearly as good and about a hundred times cheaper. Doubles
    would need the real thing (see docs/ROADMAP.md).
    """
    near: Blob | None = None
    far: Blob | None = None
    for b in blobs:
        if not court.contains(b.cx, b.cy):
            continue
        if court.side(b.cy) > 0:
            if near is None or b.area > near.area:
                near = b
        else:
            if far is None or b.area > far.area:
                far = b
    return near, far


def compute_activity(
    observations: list[FrameObservation],
    court: CourtModel,
    cfg: Config,
) -> ActivitySignal:
    """Fill in per-observation fields and return the combined signal.

    Mutates ``observations`` (sets ``player_speed``, ``spread``, ``activity``)
    so that the debug overlay can show exactly what the segmenter saw.
    """
    n = len(observations)
    if n == 0:
        empty = np.zeros(0)
        return ActivitySignal(empty, empty, empty, empty, empty, empty, empty, 0.1)

    times = np.array([o.t for o in observations], dtype=np.float64)
    dts = np.diff(times)
    sample_dt = float(np.median(dts)) if dts.size else 1.0 / max(cfg.proxy.coarse_fps, 1.0)
    if sample_dt <= 0:
        sample_dt = 1.0 / max(cfg.proxy.coarse_fps, 1.0)

    court_h = court.height
    max_match = cfg.player.match_max_dist_frac * court_h

    speeds = np.zeros(n, dtype=np.float64)
    spreads = np.zeros(n, dtype=np.float64)
    fg = np.array([o.fg_ratio for o in observations], dtype=np.float64)
    cam = np.array([o.camera_motion for o in observations], dtype=np.float64)

    prev_near: Blob | None = None
    prev_far: Blob | None = None
    prev_t = times[0]

    for i, obs in enumerate(observations):
        near, far = select_players(obs.blobs, court)
        dt = max(1e-3, obs.t - prev_t)

        def speed_of(cur: Blob | None, prev: Blob | None) -> float:
            if cur is None or prev is None:
                return 0.0
            d = float(np.hypot(cur.cx - prev.cx, cur.cy - prev.cy))
            if d > max_match:
                # Too far to be the same person between samples: a detection
                # dropout or a swap. Do not report a bogus 10 m/s sprint.
                return 0.0
            return d / court_h / dt  # court-heights per second

        speeds[i] = max(speed_of(near, prev_near), speed_of(far, prev_far))
        spreads[i] = 1.0 if (near is not None and far is not None) else 0.0

        obs.player_speed = speeds[i]
        obs.spread = spreads[i]

        prev_near, prev_far, prev_t = near, far, obs.t

    n_fg = robust_normalize(fg, cfg.activity.norm_low_pct, cfg.activity.norm_high_pct)
    n_speed = robust_normalize(speeds, cfg.activity.norm_low_pct, cfg.activity.norm_high_pct)

    raw = (
        cfg.activity.w_foreground * n_fg
        + cfg.activity.w_player_speed * n_speed
        + cfg.activity.w_spread * spreads
    )
    total_w = cfg.activity.w_foreground + cfg.activity.w_player_speed + cfg.activity.w_spread
    if total_w > 0:
        raw = raw / total_w

    window = max(1, int(round(cfg.activity.smooth_seconds / sample_dt)))
    smoothed = moving_average(raw, window)

    for i, obs in enumerate(observations):
        obs.activity = float(smoothed[i])

    return ActivitySignal(
        t=times,
        raw=raw,
        smoothed=smoothed,
        speed=n_speed,
        fg=n_fg,
        spread=spreads,
        camera_motion=cam,
        sample_dt=sample_dt,
    )

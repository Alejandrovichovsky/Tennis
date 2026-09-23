"""Per-rally feature extraction.

Everything a highlight score could plausibly want, computed from the cheap
observation stream. Each feature is documented with *why* it should correlate
with "fun to watch", because that is the part we will argue about when tuning.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import Config
from ..types import BallTrack, FrameObservation, RallyFeatures, Segment
from .activity import ActivitySignal, select_players
from .court import CourtModel
from .signals import count_peaks


@dataclass
class PlayerTracks:
    """Per-sample player positions in proxy coordinates. NaN where missing."""

    t: np.ndarray
    near_x: np.ndarray
    near_y: np.ndarray
    far_x: np.ndarray
    far_y: np.ndarray
    near_feet: np.ndarray


def build_player_tracks(observations: list[FrameObservation], court: CourtModel, cfg: Config | None = None) -> PlayerTracks:
    n = len(observations)
    t = np.zeros(n)
    nx = np.full(n, np.nan)
    ny = np.full(n, np.nan)
    fx = np.full(n, np.nan)
    fy = np.full(n, np.nan)
    nfeet = np.full(n, np.nan)
    for i, obs in enumerate(observations):
        t[i] = obs.t
        near, far = select_players(obs.blobs, court, cfg)
        if near is not None:
            nx[i], ny[i] = near.cx, near.cy
            nfeet[i] = near.cy + near.h / 2.0
        if far is not None:
            fx[i], fy[i] = far.cx, far.cy
    return PlayerTracks(t=t, near_x=nx, near_y=ny, far_x=fx, far_y=fy, near_feet=nfeet)


def _window(t: np.ndarray, start_s: float, end_s: float) -> slice:
    i0 = int(np.searchsorted(t, start_s, side="left"))
    i1 = int(np.searchsorted(t, end_s, side="right"))
    return slice(max(0, i0), max(i0 + 1, i1))


def _nan_range(values: np.ndarray) -> float:
    v = values[~np.isnan(values)]
    if v.size < 2:
        return 0.0
    return float(v.max() - v.min())


def _shots_from_ball(track: BallTrack, segment: Segment) -> float | None:
    """Count direction reversals in the ball's vertical motion.

    From behind the baseline, every stroke sends the ball the other way in
    image-y. A bounce does not: a ball flying away keeps moving up-screen
    through the bounce (it just changes speed). So reversals ~= strokes after
    the first one, and shots ~= reversals + 1. Crude but monotonic - and
    monotonic is all the ranking needs.
    """
    pts = [p for p in track.points if segment.start_s <= p.t <= segment.end_s]
    if len(pts) < 8:
        return None
    covered = (pts[-1].t - pts[0].t) / max(1e-3, segment.duration_s)
    if covered < 0.5:
        return None

    ys = np.array([p.y for p in pts])
    ts = np.array([p.t for p in pts])
    vy = np.diff(ys) / np.maximum(1e-4, np.diff(ts))
    # Ignore micro-jitter: only count reversals where the speed is meaningful.
    significant = np.abs(vy) > (0.6 * np.median(np.abs(vy)) + 1e-6)
    signs = np.sign(vy)
    reversals = 0
    last_sign = 0.0
    for s, ok in zip(signs, significant):
        if not ok or s == 0:
            continue
        if last_sign != 0.0 and s != last_sign:
            reversals += 1
        last_sign = s
    return float(reversals + 1)


def extract_features(
    segment: Segment,
    observations: list[FrameObservation],
    signal: ActivitySignal,
    tracks: PlayerTracks,
    court: CourtModel,
    cfg: Config,
    ball_track: BallTrack | None = None,
) -> RallyFeatures:
    t = signal.t
    a = signal.smoothed
    speed = signal.speed
    dt = signal.sample_dt

    sl = _window(t, segment.start_s, segment.end_s)
    seg_a = a[sl]
    seg_speed = speed[sl]
    if seg_a.size == 0:
        seg_a = np.zeros(1)
        seg_speed = np.zeros(1)

    # --- Shot count -------------------------------------------------------
    # Best source first: audible impacts, then the ball track, then bursts
    # of player motion. Audio wins because it is independent of perspective
    # and counts the far player's shots, which the ball track often loses.
    shot_source = "activity"
    shots: float | None = None
    if signal.audio_hits is not None:
        from .audio import hits_between

        heard = hits_between(signal.audio_hits, segment.start_s, segment.end_s)
        if heard >= 1:
            shots = float(heard)
            shot_source = "audio"
    if shots is None and ball_track is not None and ball_track.confidence >= cfg.ball.min_confidence:
        shots = _shots_from_ball(ball_track, segment)
        if shots is not None:
            shot_source = "ball"
    if shots is None:
        min_distance = max(1, int(round(0.35 / max(dt, 1e-3))))
        prominence = 0.12 * max(1e-6, float(seg_speed.max() - seg_speed.min()))
        shots = float(count_peaks(seg_speed, min_prominence=prominence, min_distance=min_distance))

    duration = max(1e-3, segment.duration_s)
    shot_rate = shots / duration

    # --- Intensity --------------------------------------------------------
    mean_intensity = float(np.mean(seg_a))
    peak_intensity = float(np.max(seg_a))

    # --- Coverage ---------------------------------------------------------
    # How much of the court width each player used. A rally that drags people
    # corner to corner looks better than two people hitting from one spot.
    cov_near = _nan_range(tracks.near_x[sl]) / court.width
    cov_far = _nan_range(tracks.far_x[sl]) / court.width
    coverage = float(np.clip((cov_near + cov_far) / 2.0 / 0.55, 0.0, 1.0))

    # --- Finish -----------------------------------------------------------
    # A winner: a hard last shot, then everything stops. An error: the rally
    # just fades. So compare the last 1.5 s against the rally mean, and reward
    # stillness in the 2 s *after* the segment ends.
    tail = a[_window(t, max(segment.start_s, segment.end_s - 1.5), segment.end_s)]
    tail_ratio = float(np.max(tail) / (mean_intensity + 1e-6)) if tail.size else 0.0
    after = a[_window(t, segment.end_s, segment.end_s + 2.0)]
    after_drop = 1.0 - float(np.mean(after) / (mean_intensity + 1e-6)) if after.size else 0.0
    finish = float(np.clip(0.5 * np.clip(tail_ratio - 0.6, 0, 1) + 0.5 * np.clip(after_drop, 0, 1), 0, 1))

    # --- Serve onset ------------------------------------------------------
    # A point starts from stillness: the server bounces the ball, then a sharp
    # burst. Compare the 2 s before the segment with the first 1.5 s of it.
    before = a[_window(t, max(0.0, segment.start_s - 2.0), segment.start_s)]
    head = a[_window(t, segment.start_s, segment.start_s + 1.5)]
    quiet_before = 1.0 - float(np.mean(before) / (mean_intensity + 1e-6)) if before.size else 0.5
    onset_rise = float(np.max(head) - np.min(head)) if head.size else 0.0
    serve_onset = float(
        np.clip(0.6 * np.clip(quiet_before, 0, 1) + 0.4 * np.clip(onset_rise / 0.35, 0, 1), 0, 1)
    )

    # --- Net approach -----------------------------------------------------
    # Near player only, measured at the feet, as a fraction of the *near*
    # half's on-screen depth. The far half is squashed to a few pixels by
    # perspective, so any far-player distance to the net line is meaningless
    # (and the far player's centroid always sits "at the net").
    # Skip the last 2 s: segments end late, and walking to the net to pick
    # up a ball after the point is not net play.
    core = _window(t, segment.start_s, max(segment.start_s + 1.0, segment.end_s - 2.0))
    near_feet = tracks.near_feet[core]
    v = near_feet[~np.isnan(near_feet)]
    near_half = max(1.0, court.y1 - court.net_y)
    if v.size:
        # A low percentile, not the minimum. The background model now and
        # then splits the near player and reports only his torso, whose
        # "feet" land mid-court; one such frame was enough to label a
        # baseline rally as net play, which happened to a third of all
        # points on real footage. Asking how close he got *consistently*
        # costs nothing and is not hostage to a single bad blob.
        dist = np.abs(v - court.net_y) / near_half
        nearest = float(np.percentile(dist, cfg.scoring.net_play_percentile))
    else:
        nearest = 1.0
    net_approach = float(np.clip(1.0 - nearest / max(1e-6, cfg.scoring.net_play_depth_frac), 0.0, 1.0))

    return RallyFeatures(
        duration_s=duration,
        shot_count=float(shots),
        shot_rate=float(shot_rate),
        mean_intensity=mean_intensity,
        peak_intensity=peak_intensity,
        coverage=coverage,
        finish=finish,
        serve_onset=serve_onset,
        net_approach=net_approach,
        shot_source=shot_source,
    )

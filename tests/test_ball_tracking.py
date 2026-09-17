"""Trajectory linking on synthetic candidate streams.

We feed ``link_tracks`` hand-made candidates: a parabola with noise and a
few dropouts, plus random clutter, and check that the ball chain comes out
on top with a sensible confidence - and that pure clutter does not.
"""

from __future__ import annotations

import numpy as np

from tennishl.config import Config
from tennishl.analysis.ball import BallCandidate, link_tracks, stitch_tracks


def parabola_candidates(n: int = 30, *, fps: float = 30.0, dropouts: set[int] = frozenset(),
                        clutter_per_frame: int = 0, noise_px: float = 0.5, seed: int = 3,
                        t0: float = 0.0, x0: float = 100.0, y0: float = 400.0):
    rng = np.random.default_rng(seed)
    frames: list[list[BallCandidate]] = []
    for i in range(n):
        t = t0 + i / fps
        cands: list[BallCandidate] = []
        if i not in dropouts:
            x = x0 + 18.0 * i + rng.normal(0, noise_px)
            y = y0 - 22.0 * i + 0.9 * i * i + rng.normal(0, noise_px)
            cands.append(BallCandidate(frame=i, t=t, x=x, y=y, area=20, radius=2.5, brightness=0.9))
        for _ in range(clutter_per_frame):
            cands.append(BallCandidate(frame=i, t=t, x=rng.uniform(0, 900), y=rng.uniform(0, 500),
                                       area=15, radius=2.2, brightness=rng.uniform(0.2, 0.9)))
        frames.append(cands)
    return frames


def test_clean_parabola_is_tracked_with_high_confidence():
    cfg = Config()
    tracks = link_tracks(parabola_candidates(), cfg)
    assert tracks, "no track found"
    points, conf, rms = tracks[0]
    assert len(points) == 30
    assert conf > 0.85
    assert rms < 2.0


def test_dropouts_are_bridged():
    cfg = Config()
    tracks = link_tracks(parabola_candidates(dropouts={7, 8, 15}), cfg)
    points, conf, _ = tracks[0]
    assert len(points) == 27
    assert points[-1].frame == 29
    assert conf > 0.75


def test_gap_longer_than_allowed_splits_the_chain():
    cfg = Config()
    gap = set(range(10, 10 + cfg.ball.max_gap_frames + 3))
    tracks = link_tracks(parabola_candidates(dropouts=gap), cfg)
    assert len(tracks) >= 2
    assert all(len(p) < 30 for p, _, _ in tracks)


def test_clutter_does_not_beat_the_ball():
    cfg = Config()
    tracks = link_tracks(parabola_candidates(clutter_per_frame=6), cfg)
    points, conf, _ = tracks[0]
    # The best chain should be (mostly) the parabola.
    xs = np.array([p.x for p in points])
    assert len(points) >= 24
    assert np.all(np.diff(xs) > 0)  # monotonic in x like our synthetic ball
    assert conf > 0.7


def test_pure_clutter_yields_low_confidence_or_nothing():
    cfg = Config()
    frames = parabola_candidates(dropouts=set(range(30)), clutter_per_frame=8)
    tracks = link_tracks(frames, cfg)
    assert all(conf < cfg.ball.min_confidence for _, conf, _ in tracks)


def test_stitch_concatenates_trusted_chains_without_interpolating_gap():
    cfg = Config()
    a = parabola_candidates(n=20, t0=0.0)
    b = parabola_candidates(n=20, t0=2.0, x0=600.0, y0=100.0)
    tracks = link_tracks(a, cfg) + link_tracks(b, cfg)
    track = stitch_tracks(tracks, cfg, proxy_w=960, proxy_h=540, fps=30.0)
    assert track is not None
    ts = [p.t for p in track.points]
    assert ts == sorted(ts)
    # No points were invented inside the 1.3 s hole between the chains.
    assert not any(0.7 < t < 2.0 for t in ts)
    assert track.confidence >= cfg.ball.min_confidence


def test_stitch_returns_best_low_confidence_chain_when_nothing_trusted():
    cfg = Config()
    frames = parabola_candidates(dropouts=set(range(30)), clutter_per_frame=8)
    tracks = link_tracks(frames, cfg)
    if not tracks:
        return  # nothing to stitch is a valid outcome
    track = stitch_tracks(tracks, cfg, proxy_w=960, proxy_h=540, fps=30.0)
    assert track is not None
    assert track.confidence < cfg.ball.min_confidence

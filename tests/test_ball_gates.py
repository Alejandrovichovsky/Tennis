"""The gates that separate a flying ball from a moving body.

These encode what real footage taught us: a ball fits a local quadratic to
a fraction of a pixel, a chain crawling along a torso does not; and a chain
must never be *seeded* on a player even though the ball is allowed to fly
across one.
"""

from __future__ import annotations

import numpy as np

from tennishl.config import Config
from tennishl.analysis.ball import BallCandidate, link_tracks, scaled_config, stitch_tracks


def cand(i, t, x, y, on_player=False):
    return BallCandidate(frame=i, t=t, x=x, y=y, area=20, radius=2.5,
                         brightness=0.9, on_player=on_player)


def ball_chain(n=25, fps=30.0, t0=0.0, x0=100.0, y0=400.0, on_player=False, jitter=0.0, seed=1):
    """A clean parabola - what a real flight looks like."""
    rng = np.random.default_rng(seed)
    frames = []
    for i in range(n):
        x = x0 + 18.0 * i + rng.normal(0, jitter)
        y = y0 - 22.0 * i + 0.9 * i * i + rng.normal(0, jitter)
        frames.append([cand(i, t0 + i / fps, x, y, on_player)])
    return frames


def body_chain(n=25, fps=30.0, seed=2):
    """A dense, jagged walk - what a torso looks like to the differencer."""
    rng = np.random.default_rng(seed)
    frames, x, y = [], 500.0, 300.0
    for i in range(n):
        x += rng.normal(0, 6.0)
        y += rng.normal(0, 6.0)
        frames.append([cand(i, i / fps, x, y, on_player=True)])
    return frames


def test_clean_ball_survives_the_residual_gate():
    cfg = Config()
    tracks = link_tracks(ball_chain(jitter=0.3), cfg)
    assert tracks, "a clean parabola must be tracked"
    pts, conf, rms = tracks[0]
    assert rms <= cfg.ball.max_rms_px
    assert conf >= cfg.ball.min_confidence


def test_jagged_body_is_rejected():
    cfg = Config()
    assert link_tracks(body_chain(), cfg) == []


def test_chain_is_never_seeded_on_a_player():
    """Even a perfect parabola is ignored when every point sits on a body."""
    cfg = Config()
    assert link_tracks(ball_chain(on_player=True), cfg) == []


def test_ball_may_fly_across_a_player():
    """A flight that starts free and passes over a body is kept."""
    cfg = Config()
    frames = ball_chain(n=25, jitter=0.2)
    for i in range(10, 16):            # middle of the flight crosses a player
        frames[i][0].on_player = True
    tracks = link_tracks(frames, cfg)
    assert tracks, "the ball must survive crossing a player"
    assert len(tracks[0][0]) >= 20


def test_too_much_of_the_chain_on_a_player_is_rejected():
    cfg = Config()
    frames = ball_chain(n=25, jitter=0.2)
    for i in range(4, 25):             # 84% on a body
        frames[i][0].on_player = True
    assert link_tracks(frames, cfg) == []


def test_thresholds_scale_with_proxy_width():
    cfg = Config()
    wide = scaled_config(cfg, 1920)
    assert wide.ball.max_rms_px == cfg.ball.max_rms_px * 2.0
    assert wide.ball.max_speed_px_per_frame == cfg.ball.max_speed_px_per_frame * 2.0
    # Areas are two-dimensional.
    assert wide.ball.max_area_px == cfg.ball.max_area_px * 4.0
    assert scaled_config(cfg, cfg.ball.reference_width) is cfg


# --- joining chains across a lost stretch --------------------------------

def two_chains(gap_s: float, *, deviation_px: float = 0.0):
    """Two chains from one continuous flight, separated by ``gap_s``."""
    fps = 30.0
    a = link_tracks(ball_chain(n=12, fps=fps, t0=0.0, jitter=0.2), Config())
    # Second chain continues where the first would have gone.
    n1, dx, dy = 12, 18.0, -22.0
    t1 = (n1 - 1) / fps + gap_s
    steps = n1 + int(round(gap_s * fps))
    x1 = 100.0 + dx * steps + deviation_px
    y1 = 400.0 + dy * steps + 0.9 * steps * steps
    b = link_tracks(ball_chain(n=12, fps=fps, t0=t1, x0=x1, y0=y1, jitter=0.2), Config())
    return a + b


def test_silent_gap_is_bridged():
    cfg = Config()
    tracks = two_chains(0.3)
    assert len(tracks) == 2
    track = stitch_tracks(tracks, cfg, proxy_w=1280, proxy_h=720, fps=30.0,
                          audio_hits=np.array([]))
    ts = [p.t for p in track.points]
    assert ts == sorted(ts)
    # The hole is filled with interpolated points.
    assert any(p.interpolated for p in track.points)
    assert max(np.diff(ts)) < 0.1


def test_gap_containing_a_hit_is_left_alone():
    """A racket touched the ball: two flights, not one lost one."""
    cfg = Config()
    tracks = two_chains(0.3)
    mid = tracks[0][0][-1].t + 0.15
    track = stitch_tracks(tracks, cfg, proxy_w=1280, proxy_h=720, fps=30.0,
                          audio_hits=np.array([mid]))
    ts = np.array([p.t for p in track.points])
    assert max(np.diff(ts)) > 0.25


def test_gap_is_left_alone_when_the_ball_reappears_elsewhere():
    cfg = Config()
    tracks = two_chains(0.3, deviation_px=400.0)
    track = stitch_tracks(tracks, cfg, proxy_w=1280, proxy_h=720, fps=30.0,
                          audio_hits=np.array([]))
    ts = np.array([p.t for p in track.points])
    assert max(np.diff(ts)) > 0.25


def test_bridging_works_without_audio():
    """The parabola test stands on its own, so silent footage still works."""
    cfg = Config()
    tracks = two_chains(0.3)
    track = stitch_tracks(tracks, cfg, proxy_w=1280, proxy_h=720, fps=30.0, audio_hits=None)
    ts = np.array([p.t for p in track.points])
    assert max(np.diff(ts)) < 0.1


def test_gap_longer_than_the_limit_is_never_joined():
    cfg = Config()
    tracks = two_chains(cfg.ball.join_max_gap_s + 0.3)
    track = stitch_tracks(tracks, cfg, proxy_w=1280, proxy_h=720, fps=30.0, audio_hits=None)
    ts = np.array([p.t for p in track.points])
    assert max(np.diff(ts)) > cfg.ball.join_max_gap_s

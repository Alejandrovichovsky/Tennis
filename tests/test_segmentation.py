"""Rally segmentation on synthetic activity signals.

No video involved: we build a(t) by hand with known bursts and check that the
segmenter finds them, respects hysteresis, merges dropouts and rejects the
right things for the right reasons.
"""

from __future__ import annotations

import numpy as np
import pytest

from tennishl.config import Config
from tennishl.analysis.segmentation import compute_thresholds, segment_signal


def make_signal(duration_s: float, bursts: list[tuple[float, float]], *, dt: float = 0.1,
                base: float = 0.05, high: float = 0.8, noise: float = 0.02, seed: int = 1):
    rng = np.random.default_rng(seed)
    t = np.arange(0.0, duration_s, dt)
    a = np.full_like(t, base) + rng.normal(0, noise, size=t.size)
    for s, e in bursts:
        a[(t >= s) & (t <= e)] = high + rng.normal(0, noise, size=int(((t >= s) & (t <= e)).sum()))
    spread = np.ones_like(t)
    cam = np.zeros_like(t)
    return t, np.clip(a, 0, 1), spread, cam


def overlaps(seg, start, end, tol=0.6) -> bool:
    return abs(seg.start_s - start) <= tol and abs(seg.end_s - end) <= tol


def test_finds_all_bursts_with_accurate_boundaries():
    cfg = Config()
    bursts = [(10.0, 16.0), (30.0, 42.0), (60.0, 64.0)]
    t, a, spread, cam = make_signal(80.0, bursts)
    res = segment_signal(t, a, spread, cam, cfg)

    assert len(res.segments) == 3
    for seg, (s, e) in zip(res.segments, bursts):
        assert overlaps(seg, s, e), (seg, s, e)
    assert res.rejected == []


def test_thresholds_are_self_calibrating():
    """Same shape, different absolute level -> same segments."""
    cfg = Config()
    bursts = [(5.0, 12.0), (20.0, 26.0)]
    t, a, spread, cam = make_signal(40.0, bursts, base=0.05, high=0.8)
    t2, a2, _, _ = make_signal(40.0, bursts, base=0.3, high=0.5)
    r1 = segment_signal(t, a, spread, cam, cfg)
    r2 = segment_signal(t2, a2, spread, cam, cfg)
    assert len(r1.segments) == len(r2.segments) == 2
    enter, exit_ = compute_thresholds(a2, cfg)
    assert 0.3 < exit_ < enter < 0.5


def test_short_dropout_inside_a_rally_is_bridged():
    cfg = Config()
    # One 10 s rally with a 0.5 s dip in the middle (a frame where nobody moved).
    t, a, spread, cam = make_signal(30.0, [(5.0, 9.8), (10.3, 15.0)])
    res = segment_signal(t, a, spread, cam, cfg)
    assert len(res.segments) == 1
    assert overlaps(res.segments[0], 5.0, 15.0)


def test_long_gap_splits_two_rallies():
    cfg = Config()
    t, a, spread, cam = make_signal(40.0, [(5.0, 10.0), (14.0, 20.0)])
    res = segment_signal(t, a, spread, cam, cfg)
    assert len(res.segments) == 2


def test_too_short_bursts_are_rejected_with_reason():
    cfg = Config()
    t, a, spread, cam = make_signal(30.0, [(5.0, 6.0), (15.0, 22.0)])
    res = segment_signal(t, a, spread, cam, cfg)
    assert len(res.segments) == 1
    assert [r.reason for r in res.rejected] == ["too_short"]
    assert res.rejected[0].start_s == pytest.approx(5.0, abs=0.6)


def test_too_long_is_rejected():
    cfg = Config()
    t, a, spread, cam = make_signal(120.0, [(5.0, 80.0)])
    res = segment_signal(t, a, spread, cam, cfg)
    assert res.segments == []
    assert res.rejected[0].reason == "too_long"


def test_players_must_be_on_both_sides():
    cfg = Config()
    t, a, spread, cam = make_signal(40.0, [(5.0, 12.0), (20.0, 27.0)])
    spread[(t >= 20.0) & (t <= 27.0)] = 0.0  # one player left the court
    res = segment_signal(t, a, spread, cam, cfg)
    assert len(res.segments) == 1
    assert res.rejected[0].reason == "players_not_on_both_sides"


def test_camera_bump_rejects_segment():
    cfg = Config()
    t, a, spread, cam = make_signal(40.0, [(5.0, 12.0), (20.0, 27.0)])
    cam[(t >= 22.0) & (t <= 22.4)] = 0.9
    res = segment_signal(t, a, spread, cam, cfg)
    assert len(res.segments) == 1
    assert res.rejected[0].reason == "camera_moved"


def test_start_backtracks_to_onset_not_to_high_crossing():
    """A slow ramp-up should be included from where it left the floor."""
    cfg = Config()
    dt = 0.1
    t = np.arange(0.0, 40.0, dt)
    a = np.full_like(t, 0.05)
    ramp = (t >= 10.0) & (t < 13.0)
    a[ramp] = np.linspace(0.05, 0.9, ramp.sum())
    a[(t >= 13.0) & (t <= 20.0)] = 0.9
    res = segment_signal(t, a, np.ones_like(t), np.zeros_like(t), cfg)
    assert len(res.segments) == 1
    # Enter threshold is hit around t~12, but the segment must start earlier.
    assert res.segments[0].start_s < 11.5


def test_empty_signal_is_handled():
    cfg = Config()
    empty = np.zeros(0)
    res = segment_signal(empty, empty, empty, empty, cfg)
    assert res.segments == [] and res.rejected == []


def test_flat_signal_yields_nothing():
    cfg = Config()
    t = np.arange(0, 30, 0.1)
    a = np.full_like(t, 0.2)
    res = segment_signal(t, a, np.ones_like(t), np.zeros_like(t), cfg)
    assert res.segments == []

"""Audio changes the segmentation gates: no hits -> rejected; hits -> the
far player does not have to be visible."""

from __future__ import annotations

import numpy as np

from tennishl.config import Config
from tennishl.analysis.segmentation import segment_signal
from tests.test_segmentation import make_signal


def test_walk_without_hits_is_rejected_when_audio_exists():
    cfg = Config()
    t, a, spread, cam = make_signal(40.0, [(5.0, 12.0), (20.0, 27.0)])
    hits = np.array([6.0, 7.0, 8.0, 9.5, 10.5])  # only the first burst has a ball
    res = segment_signal(t, a, spread, cam, cfg, audio_hits=hits)
    assert len(res.segments) == 1
    assert res.rejected[0].reason == "no_ball_hits"


def test_hits_override_missing_far_player():
    cfg = Config()
    t, a, spread, cam = make_signal(40.0, [(5.0, 12.0)])
    spread[:] = 0.0  # far player never detected
    hits = np.array([6.0, 7.0, 8.5, 10.0])
    with_audio = segment_signal(t, a, spread, cam, cfg, audio_hits=hits)
    without = segment_signal(t, a, spread, cam, cfg, audio_hits=None)
    assert len(with_audio.segments) == 1
    assert without.segments == [] and without.rejected[0].reason == "players_not_on_both_sides"


def test_segment_end_is_trimmed_to_the_last_hit():
    """The point is over when the last ball was struck, not when the
    players have finished jogging back."""
    cfg = Config()
    t, a, spread, cam = make_signal(40.0, [(5.0, 25.0)])
    hits = np.array([6.0, 8.0, 10.0, 12.0])  # rally really ends at 12 s
    res = segment_signal(t, a, spread, cam, cfg, audio_hits=hits)
    assert len(res.segments) == 1
    end = res.segments[0].end_s
    assert abs(end - (12.0 + cfg.segmentation.tail_after_last_hit_s)) < 0.3, end


def test_trim_never_deletes_a_segment():
    """Trimming refines where a point ended; it must not decide whether
    the point happened. A single early hit used to shorten segments below
    min_duration and silently drop them."""
    cfg = Config()
    t, a, spread, cam = make_signal(40.0, [(5.0, 11.0)])
    hits = np.array([5.5])  # one hit right at the start
    res = segment_signal(t, a, spread, cam, cfg, audio_hits=hits)
    assert len(res.segments) == 1
    seg = res.segments[0]
    assert seg.duration_s >= cfg.segmentation.min_duration_s


def test_audio_alone_can_carry_a_segment():
    """Unmistakable hitting must not be vetoed by silent visual channels.

    Real case: players far from the camera barely register as foreground,
    so fg, speed and spread are all near zero while the racket impacts are
    perfectly audible. Averaging the channels lost six such rallies in ten
    minutes of footage; combining them as OR keeps them.
    """
    from tennishl.analysis.activity import compute_activity
    from tennishl.analysis.court import CourtModel
    from tennishl.types import FrameObservation

    cfg = Config()
    court = CourtModel(x0=0, y0=0, x1=480, y1=270, net_y=135,
                       proxy_size=(480, 270), confidence=1.0)
    # 40 s of nothing visible at all: no blobs, no foreground.
    obs = [FrameObservation(index=i, frame_index=i * 3, t=i * 0.1, fg_ratio=0.0,
                            camera_motion=0.0, blobs=[]) for i in range(400)]
    # One racket impact per second between 10 s and 20 s.
    hits = np.arange(10.0, 20.0, 1.0)

    sig = compute_activity(obs, court, cfg, audio_hits=hits)
    res = segment_signal(sig.t, sig.smoothed, sig.spread, sig.camera_motion,
                         cfg, audio_hits=hits)
    assert len(res.segments) == 1, res.rejected
    seg = res.segments[0]
    assert seg.start_s < 12.0 and seg.end_s > 17.0

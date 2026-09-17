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

"""Highlight scoring, categories, padding and clip merging."""

from __future__ import annotations

import pytest

from tennishl.config import Config
from tennishl.analysis.scoring import (
    apply_clip_padding,
    build_highlights,
    categorise,
    merge_adjacent_clips,
    score_features,
)
from tennishl.types import RallyFeatures, Segment


def feats(**overrides) -> RallyFeatures:
    base = dict(
        duration_s=6.0, shot_count=5.0, shot_rate=0.8, mean_intensity=0.5, peak_intensity=0.7,
        coverage=0.4, finish=0.3, serve_onset=0.3, net_approach=0.0,
    )
    base.update(overrides)
    return RallyFeatures(**base)


def seg(start: float, end: float) -> Segment:
    return Segment(start_s=start, end_s=end, mean_activity=0.6, peak_activity=0.8,
                   both_sides_frac=0.9, camera_motion=0.0)


def test_score_is_in_unit_interval_and_contributions_sum_to_score():
    cfg = Config()
    score, contrib = score_features(feats(), cfg)
    assert 0.0 <= score <= 1.0
    assert sum(contrib.values()) == pytest.approx(score, abs=1e-6)


def test_longer_rally_scores_higher_all_else_equal():
    cfg = Config()
    s_short, _ = score_features(feats(duration_s=3.0, shot_count=3.0), cfg)
    s_long, _ = score_features(feats(duration_s=15.0, shot_count=12.0), cfg)
    assert s_long > s_short


def test_duration_saturates():
    cfg = Config()
    s1, _ = score_features(feats(duration_s=20.0), cfg)
    s2, _ = score_features(feats(duration_s=40.0), cfg)
    s3, _ = score_features(feats(duration_s=80.0), cfg)
    assert s2 > s1
    assert (s3 - s2) < (s2 - s1)  # diminishing returns


def test_categories_serve_only_for_short_points():
    cfg = Config()
    assert "serve" in categorise(feats(serve_onset=0.9, shot_count=2.0), cfg)
    assert "serve" not in categorise(feats(serve_onset=0.9, shot_count=9.0), cfg)


def test_categories_long_and_fast_and_winner():
    cfg = Config()
    assert "long_rally" in categorise(feats(shot_count=12.0), cfg)
    assert "fast_exchange" in categorise(feats(shot_count=8.0, shot_rate=1.6), cfg)
    assert "winner" in categorise(feats(finish=0.8), cfg)
    assert categorise(feats(), cfg) == ["baseline_rally"]


def test_at_most_two_labels_most_specific_first():
    cfg = Config()
    cats = categorise(feats(finish=0.9, shot_count=12.0, shot_rate=2.0, net_approach=0.9), cfg)
    assert len(cats) == 2
    assert cats[0] == "winner"


def test_ranking_and_selection_limit():
    cfg = Config().merged({"clip": {"max_highlights": 2}})
    segments = [seg(0, 5), seg(20, 35), seg(50, 58)]
    features = [feats(duration_s=5, shot_count=3), feats(duration_s=15, shot_count=12), feats(duration_s=8, shot_count=6)]
    hs = build_highlights(segments, features, cfg)
    assert [h.rank for h in hs] == [1, 2, 3]
    assert hs[0].segment.start_s == 20  # the long one wins
    assert [h.selected for h in hs] == [True, True, False]


def test_padding_adds_pre_and_post_roll_and_clamps():
    cfg = Config().merged({"clip": {"pre_roll_s": 2.0, "post_roll_s": 3.0}})
    hs = build_highlights([seg(1.0, 5.0), seg(100.0, 118.0)], [feats(), feats()], cfg)
    apply_clip_padding(hs, cfg, video_duration_s=120.0)
    by_start = sorted(hs, key=lambda h: h.segment.start_s)
    assert by_start[0].clip_start_s == 0.0            # clamped at 0
    assert by_start[0].clip_end_s == pytest.approx(8.0)
    assert by_start[1].clip_start_s == pytest.approx(98.0)
    assert by_start[1].clip_end_s == pytest.approx(120.0)  # clamped at end


def test_overlapping_padded_clips_split_at_midpoint():
    cfg = Config().merged({"clip": {"pre_roll_s": 2.0, "post_roll_s": 2.5}})
    # Gap of 3 s between points; padding would overlap by 1.5 s.
    hs = build_highlights([seg(10.0, 20.0), seg(23.0, 30.0)], [feats(), feats()], cfg)
    apply_clip_padding(hs, cfg, video_duration_s=60.0)
    a, b = sorted(hs, key=lambda h: h.segment.start_s)
    assert a.clip_end_s == pytest.approx(21.5)
    assert b.clip_start_s == pytest.approx(21.5)
    assert a.clip_end_s <= b.clip_start_s


def test_adjacent_points_merge_into_one_clip_keeping_best():
    cfg = Config().merged({"clip": {"min_clip_gap_s": 2.5}})
    hs = build_highlights(
        [seg(10.0, 20.0), seg(21.0, 30.0), seg(60.0, 70.0)],
        [feats(duration_s=10), feats(duration_s=9, shot_count=12), feats()],
        cfg,
    )
    apply_clip_padding(hs, cfg, video_duration_s=100.0)
    merged = merge_adjacent_clips(hs, cfg)
    assert len(merged) == 2
    first = merged[0]
    assert first.segment.start_s == 21.0        # higher score survived
    assert first.clip_start_s == pytest.approx(8.0)   # but absorbed the earlier clip
    assert first.clip_end_s == pytest.approx(32.5)
    assert sum(1 for h in hs if h.selected) == 2


def test_far_apart_points_are_not_merged():
    cfg = Config()
    hs = build_highlights([seg(10.0, 20.0), seg(26.0, 30.0)], [feats(), feats()], cfg)
    apply_clip_padding(hs, cfg, video_duration_s=100.0)
    assert len(merge_adjacent_clips(hs, cfg)) == 2


def test_config_merge_rejects_unknown_keys():
    with pytest.raises(ValueError):
        Config().merged({"clip": {"nope": 1}})

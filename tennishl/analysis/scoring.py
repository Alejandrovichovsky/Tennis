"""Highlight scoring and (approximate) categorisation.

Design stance for the MVP: a transparent weighted sum, not a model. Every
contribution is stored alongside the score so that when a clip looks wrong we
can see *which* feature pushed it up, instead of shrugging at a black box.
Swapping this for a learned ranker later means replacing one function.

Categories are labels for the UI, not claims about tennis. "forehand-like" is
honestly out of reach without pose estimation, so we label what we can
actually measure: how the point started, how long the exchange was, how fast
it was, whether someone came in, and how it ended.
"""

from __future__ import annotations

import numpy as np

from ..config import Config
from ..types import Highlight, RallyFeatures, Segment
from .signals import saturating

CATEGORY_LABELS_SV = {
    "serve": "Serve",
    "long_rally": "Lång duell",
    "fast_exchange": "Snabbt utbyte",
    "winner": "Vinnande slag",
    "net_play": "Nätspel",
    "baseline_rally": "Grundslagsduell",
    "rally": "Poäng",
}


def score_features(features: RallyFeatures, cfg: Config) -> tuple[float, dict[str, float]]:
    s = cfg.scoring
    parts = {
        "duration": s.w_duration * saturating(features.duration_s, s.duration_saturation_s),
        "shots": s.w_shots * saturating(features.shot_count, s.shots_saturation),
        "intensity": s.w_intensity
        * float(np.clip(0.5 * features.mean_intensity + 0.5 * features.peak_intensity, 0, 1)),
        "coverage": s.w_coverage * features.coverage,
        "finish": s.w_finish * features.finish,
        "serve": s.w_serve * features.serve_onset,
    }
    total_w = (
        s.w_duration + s.w_shots + s.w_intensity + s.w_coverage + s.w_finish + s.w_serve
    )
    if total_w <= 0:
        return 0.0, parts
    score = sum(parts.values()) / total_w
    normalised = {k: v / total_w for k, v in parts.items()}
    return float(np.clip(score, 0.0, 1.0)), normalised


def categorise(features: RallyFeatures, cfg: Config) -> list[str]:
    """At most two labels, most specific first.

    "serve" means a *serve highlight* - a point decided by the serve (ace or
    service winner): a clear start-from-stillness and almost no exchange.
    A 12-shot rally also starts with a serve, but nobody calls it one.
    """
    s = cfg.scoring
    cats: list[str] = []

    if features.serve_onset >= s.serve_onset_score and features.shot_count <= 3:
        cats.append("serve")
    if features.finish >= s.winner_finish_score and features.shot_count >= 3:
        cats.append("winner")
    if features.shot_count >= s.long_rally_shots:
        cats.append("long_rally")
    if features.net_approach >= 0.5:
        cats.append("net_play")
    if features.shot_rate >= s.fast_exchange_shot_rate and features.shot_count >= 4:
        cats.append("fast_exchange")

    if not cats:
        cats.append("baseline_rally")
    return cats[:2]


def build_highlights(
    segments: list[Segment],
    features: list[RallyFeatures],
    cfg: Config,
) -> list[Highlight]:
    """Score, categorise and rank. Returns the full list, best first."""
    if len(segments) != len(features):
        raise ValueError("segments and features must be the same length")

    highlights: list[Highlight] = []
    for i, (seg, feat) in enumerate(zip(segments, features)):
        score, contributions = score_features(feat, cfg)
        highlights.append(
            Highlight(
                id=f"h{i:03d}",
                segment=seg,
                features=feat,
                score=score,
                categories=categorise(feat, cfg),
                contributions=contributions,
            )
        )

    highlights.sort(key=lambda h: h.score, reverse=True)
    for rank, h in enumerate(highlights, start=1):
        h.rank = rank
        h.selected = rank <= cfg.clip.max_highlights
    return highlights


def apply_clip_padding(highlights: list[Highlight], cfg: Config, video_duration_s: float) -> None:
    """Add pre/post-roll and make sure selected clips do not overlap.

    Padding is what makes a cut feel like a highlight instead of a jump cut:
    you want to see the server toss, and you want the moment after the winner
    where someone throws their arms up.
    """
    for h in highlights:
        h.clip_start_s = max(0.0, h.segment.start_s - cfg.clip.pre_roll_s)
        end = h.segment.end_s + cfg.clip.post_roll_s
        h.clip_end_s = min(video_duration_s, end) if video_duration_s > 0 else end

    # Resolve overlaps in chronological order among selected clips only.
    chosen = sorted([h for h in highlights if h.selected], key=lambda h: h.clip_start_s)
    for prev, cur in zip(chosen, chosen[1:]):
        if cur.clip_start_s < prev.clip_end_s:
            midpoint = (prev.segment.end_s + cur.segment.start_s) / 2.0
            midpoint = min(max(midpoint, prev.segment.end_s), cur.segment.start_s)
            prev.clip_end_s = max(prev.segment.end_s, midpoint)
            cur.clip_start_s = min(cur.segment.start_s, midpoint)


def merge_adjacent_clips(highlights: list[Highlight], cfg: Config) -> list[Highlight]:
    """Two *points* separated by less than ``min_clip_gap_s`` become one clip.

    Cutting away 0.4 s between two rallies looks like a glitch, not an edit.
    The gap is measured between the segments themselves, not the padded
    clips: padded windows that merely touch are handled by
    ``apply_clip_padding`` (a hard cut in the dead time) and must not swallow
    each other. The higher-scoring highlight survives and absorbs the other.
    """
    chosen = sorted([h for h in highlights if h.selected], key=lambda h: h.clip_start_s)
    if not chosen:
        return []

    merged: list[Highlight] = [chosen[0]]
    for cur in chosen[1:]:
        prev = merged[-1]
        gap = cur.segment.start_s - prev.segment.end_s
        if gap > cfg.clip.min_clip_gap_s:
            merged.append(cur)
            continue

        keep, drop = (prev, cur) if prev.score >= cur.score else (cur, prev)
        keep.clip_start_s = min(prev.clip_start_s, cur.clip_start_s)
        keep.clip_end_s = max(prev.clip_end_s, cur.clip_end_s)
        keep.categories = sorted(set(prev.categories) | set(cur.categories))
        drop.selected = False
        merged[-1] = keep
    return merged

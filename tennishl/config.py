"""Central configuration.

Every tunable number in the pipeline lives here so we can tweak heuristics
without touching algorithm code. Load overrides from JSON with
``Config.load("my_tuning.json")``.

Keep this file boring: plain dataclasses, no logic.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


@dataclass
class ProxyConfig:
    """Downsampling used for analysis. We never analyse at 1080p."""

    # Coarse pass: activity + player blobs. Small and fast.
    coarse_width: int = 480
    coarse_fps: float = 10.0

    # Fine pass: ball tracking inside rally windows only. Needs more pixels
    # because a tennis ball at 480px wide is ~3px across. Measured on real
    # 1080p footage: 960 finds a candidate in 73% of frames, 1280 in 90%.
    ball_width: int = 1280
    ball_fps: float = 0.0  # 0 => native fps (ball needs every frame)


@dataclass
class PlayerConfig:
    """Player blob detection on the foreground mask."""

    # Areas as a fraction of proxy frame area. Perspective is brutal from
    # behind the baseline: the near player is 0.5-3% of the frame, the far
    # player 0.01-0.1% (about 14x5 px at 480 wide). The coarse pass keeps
    # everything above ``min_area_frac``; the per-side floors are applied
    # once we know which side of the net a blob stands on.
    min_area_frac: float = 0.00008
    min_area_frac_near: float = 0.0006
    min_area_frac_far: float = 0.00008
    # The coarse pass keeps very large blobs too (the near player walking
    # up to the lens fills a third of the frame): they are not a *playing*
    # player, but the ball pass must know where they are to ignore them.
    max_area_frac: float = 0.5
    max_area_frac_near: float = 0.06   # bigger than this = not on court, ignore for play
    max_area_frac_far: float = 0.012  # the near player's torso must not pass as "far"
    min_aspect: float = 0.7          # height/width; people are tall-ish
    max_blobs_per_frame: int = 16    # cap bookkeeping on noisy frames
    match_max_dist_frac: float = 0.12  # frame-height fraction for frame-to-frame matching


@dataclass
class CourtConfig:
    """Region of interest + net line, derived from accumulated motion."""

    motion_percentile: float = 92.0   # keep the hottest motion pixels
    roi_margin_frac: float = 0.06     # expand the bbox a bit
    min_roi_area_frac: float = 0.05   # sanity floor; below this we use full frame


@dataclass
class AudioConfig:
    """Ball-hit detection from the soundtrack (see analysis/audio.py)."""

    enabled: bool = True
    sensitivity: float = 7.0        # robust sigmas above the positive-onset floor
    min_hit_spacing_s: float = 0.22  # two hits closer than this are one
    density_window_s: float = 1.5   # +- window for hits/s in a(t)
    min_hits_per_segment: int = 1   # with audio, a "rally" without a hit is not one


@dataclass
class ActivityConfig:
    """Weights for the scalar activity signal a(t)."""

    w_foreground: float = 0.25
    w_player_speed: float = 0.35
    w_spread: float = 0.10
    w_audio: float = 0.30           # used only when the file has audio
    smooth_seconds: float = 0.8
    norm_low_pct: float = 20.0
    norm_high_pct: float = 92.0


@dataclass
class SegmentationConfig:
    """Hysteresis thresholding that turns a(t) into rally segments."""

    enter_frac: float = 0.55   # of the robust dynamic range -> start a rally
    exit_frac: float = 0.32    # -> end a rally (lower = hysteresis)
    min_duration_s: float = 2.5
    max_duration_s: float = 45.0
    # Bridge dropouts inside one point, and pull the pre-serve ball bounces
    # into the point that follows. Real points are never closer than ~5 s
    # (a second serve after a fault is the tightest case), so 2.5 s is safe.
    merge_gap_s: float = 2.5
    min_gap_s: float = 0.6     # dwell time below exit before we close a rally
    # Share of frames with players on both sides. Weak on real footage (the
    # far player is a few pixels wide), so with audio present a segment
    # passes if it has *either* this or enough ball hits.
    require_both_sides_frac: float = 0.45
    # A point is over when the last ball was struck. Without this the
    # segment runs on while the players jog back, which put clip ends
    # 2-6 s past the real end (median 1.8 s) on real footage.
    tail_after_last_hit_s: float = 0.8
    max_camera_motion: float = 0.35        # drop segments where the phone was moved


@dataclass
class BallConfig:
    """Small-fast-object detection and temporal trajectory linking."""

    diff_threshold: int = 14          # 3-frame difference threshold (0-255)
    min_area_px: float = 2.0          # at ball_width proxy scale
    # A 1080p ball is ~8-15 px across -> ~4-8 px at 960 wide -> 12-50 px of
    # area, but blur and the near-court perspective can double that.
    max_area_px: float = 160.0
    max_fill_ratio: float = 1.4       # area vs bbox area sanity (blobby, not streaky lines)
    player_exclusion_pad: int = 6     # px around player boxes we ignore
    max_candidates_per_frame: int = 24

    # All pixel values below are expressed at ``reference_width`` and scaled
    # automatically to whatever ``proxy.ball_width`` is in use, so changing
    # the analysis resolution never invalidates a tuning file.
    reference_width: int = 960
    # Frame-counting thresholds below are declared at this frame rate and
    # rescaled to the clip's own, so 60 fps footage is not silently held to
    # gates meant for 30 fps: the ball moves half as far between frames,
    # and the same number of frames covers half the time.
    reference_fps: float = 30.0

    max_speed_px_per_frame: float = 55.0  # gating for linking
    max_gap_frames: int = 6               # allow short occlusions (0.2 s at 30 fps)
    min_track_points: int = 6
    full_length_points: int = 20          # chain length that counts as "long"
    accel_tolerance_px: float = 6.0       # softness of the smoothness score
    # Hard gate. Measured on real footage: a flying ball fits a local
    # quadratic to 0.09-0.30 px, a chain crawling along a body to 1.1-6.0.
    max_rms_px: float = 0.9
    max_on_player_frac: float = 0.4       # more than this and it IS the player

    # Joining two chains across a lost stretch (see stitch_tracks).
    join_max_gap_s: float = 0.6
    join_hit_margin_s: float = 0.08       # a hit this close to the gap blocks a join
    join_tolerance_px: float = 45.0       # how far off the prediction may be

    min_confidence: float = 0.6           # below this we do NOT draw a trail
    trail_seconds: float = 0.45


@dataclass
class ScoringConfig:
    """How highlight-worthy a rally is. Weights sum is irrelevant (normalised)."""

    w_duration: float = 0.22
    w_shots: float = 0.24
    w_intensity: float = 0.18
    w_coverage: float = 0.12
    w_finish: float = 0.16
    w_serve: float = 0.08

    duration_saturation_s: float = 22.0  # beyond this, longer stops helping
    shots_saturation: float = 14.0

    # Category thresholds (approximate on purpose - see README)
    long_rally_shots: float = 8.0
    fast_exchange_shot_rate: float = 1.4   # shots per second
    winner_finish_score: float = 0.62
    serve_onset_score: float = 0.6
    net_play_depth_frac: float = 0.2       # near player's feet within this fraction of the near half's depth
    net_play_percentile: float = 10.0      # how close he got consistently, not once


@dataclass
class ClipConfig:
    """Cutting and export."""

    pre_roll_s: float = 2.0
    post_roll_s: float = 2.5
    min_clip_gap_s: float = 2.5    # points closer than this -> one clip
    # "all": every detected rally, dead time removed (the TennisCut default:
    #        "two hours on court becomes two minutes of rallies").
    # "top": only the best ``max_highlights`` rallies.
    select_mode: str = "all"
    max_highlights: int = 12
    fade_s: float = 0.25
    crf: int = 20
    preset: str = "veryfast"
    audio_bitrate: str = "128k"
    draw_ball_trail: bool = True


@dataclass
class Config:
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    player: PlayerConfig = field(default_factory=PlayerConfig)
    court: CourtConfig = field(default_factory=CourtConfig)
    activity: ActivityConfig = field(default_factory=ActivityConfig)
    segmentation: SegmentationConfig = field(default_factory=SegmentationConfig)
    ball: BallConfig = field(default_factory=BallConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    clip: ClipConfig = field(default_factory=ClipConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        cfg = cls()
        if path is None:
            return cfg
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cfg.merged(data)

    def merged(self, overrides: dict[str, Any]) -> "Config":
        """Return a copy with a (possibly partial) nested dict applied."""
        return _merge_dataclass(self, overrides)


def _merge_dataclass(obj: Any, overrides: dict[str, Any]) -> Any:
    if not is_dataclass(obj):
        return overrides
    kwargs: dict[str, Any] = {}
    valid = {f.name for f in fields(obj)}
    unknown = set(overrides) - valid
    if unknown:
        raise ValueError(f"Unknown config keys for {type(obj).__name__}: {sorted(unknown)}")
    for f in fields(obj):
        current = getattr(obj, f.name)
        if f.name in overrides:
            value = overrides[f.name]
            kwargs[f.name] = (
                _merge_dataclass(current, value)
                if is_dataclass(current) and isinstance(value, dict)
                else value
            )
        else:
            kwargs[f.name] = current
    return type(obj)(**kwargs)

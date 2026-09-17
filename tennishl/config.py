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
    # because a tennis ball at 480px wide is ~3px across.
    ball_width: int = 960
    ball_fps: float = 0.0  # 0 => native fps (ball needs every frame)


@dataclass
class PlayerConfig:
    """Player blob detection on the foreground mask."""

    # Areas as a fraction of proxy frame area. A player at 480px wide from a
    # tripod behind the baseline is roughly 0.1%-3% of the frame.
    min_area_frac: float = 0.0006
    max_area_frac: float = 0.06
    min_aspect: float = 0.7          # height/width; people are tall-ish
    max_blobs_per_frame: int = 12    # cap bookkeeping on noisy frames
    match_max_dist_frac: float = 0.12  # frame-height fraction for frame-to-frame matching


@dataclass
class CourtConfig:
    """Region of interest + net line, derived from accumulated motion."""

    motion_percentile: float = 92.0   # keep the hottest motion pixels
    roi_margin_frac: float = 0.06     # expand the bbox a bit
    min_roi_area_frac: float = 0.05   # sanity floor; below this we use full frame


@dataclass
class ActivityConfig:
    """Weights for the scalar activity signal a(t)."""

    w_foreground: float = 0.35
    w_player_speed: float = 0.45
    w_spread: float = 0.20
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
    merge_gap_s: float = 1.2   # bridge short dropouts inside one rally
    min_gap_s: float = 0.6     # dwell time below exit before we close a rally
    require_both_sides_frac: float = 0.45  # share of frames with players on both sides
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

    max_speed_px_per_frame: float = 55.0  # gating for linking
    max_gap_frames: int = 3               # allow short occlusions
    min_track_points: int = 6
    full_length_points: int = 20          # chain length that counts as "long"
    accel_tolerance_px: float = 6.0       # residual of local quadratic fit; a
                                          # real flight sits at ~0.5-3 px
    min_confidence: float = 0.55          # below this we do NOT draw a trail
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
    net_play_depth_frac: float = 0.22      # distance to net line, frame-height fraction


@dataclass
class ClipConfig:
    """Cutting and export."""

    pre_roll_s: float = 2.0
    post_roll_s: float = 2.5
    min_clip_gap_s: float = 2.5    # points closer than this -> one clip
    max_highlights: int = 12
    fade_s: float = 0.25
    crf: int = 20
    preset: str = "veryfast"
    audio_bitrate: str = "128k"
    draw_ball_trail: bool = True


@dataclass
class Config:
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
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

"""Data structures passed between pipeline stages.

These are deliberately plain: no OpenCV, no numpy arrays inside them (apart
from where noted). They are what we serialise to JSON for debugging and what
a future Swift port would mirror one-to-one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration_s: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Blob:
    """A connected foreground component in proxy coordinates."""

    cx: float
    cy: float
    w: float
    h: float
    area: float

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.cx - self.w / 2, self.cy - self.h / 2, self.w, self.h)


@dataclass
class FrameObservation:
    """Everything the coarse pass records for one sampled frame.

    Cheap enough to keep in memory for a 2h match: at 10 fps that is 72k of
    these, a few tens of MB.
    """

    index: int          # index into the sampled sequence
    frame_index: int    # index in the source video
    t: float            # seconds
    fg_ratio: float     # foreground pixels / ROI pixels
    camera_motion: float  # global diff, spots a moved tripod or a scene cut
    blobs: list[Blob] = field(default_factory=list)

    # Filled in after the ROI/net line is known (second, cheap, in-memory pass)
    player_speed: float = 0.0     # max player centroid speed, frame-heights/s
    spread: float = 0.0           # 1.0 when players are on both sides of the net
    activity: float = 0.0         # smoothed scalar signal a(t)


@dataclass
class Segment:
    """A stretch of active play (a rally / point)."""

    start_s: float
    end_s: float
    mean_activity: float
    peak_activity: float
    both_sides_frac: float
    camera_motion: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration_s"] = self.duration_s
        return d


@dataclass
class BallPoint:
    t: float
    x: float          # normalised 0..1 of source frame width
    y: float          # normalised 0..1 of source frame height
    radius_px: float  # in the ball-pass proxy scale
    interpolated: bool = False


@dataclass
class BallTrack:
    points: list[BallPoint]
    confidence: float
    rms_residual_px: float

    @property
    def start_s(self) -> float:
        return self.points[0].t if self.points else 0.0

    @property
    def end_s(self) -> float:
        return self.points[-1].t if self.points else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": round(self.confidence, 4),
            "rms_residual_px": round(self.rms_residual_px, 3),
            "start_s": round(self.start_s, 3),
            "end_s": round(self.end_s, 3),
            "points": [
                {
                    "t": round(p.t, 4),
                    "x": round(p.x, 5),
                    "y": round(p.y, 5),
                    "r": round(p.radius_px, 2),
                    "i": p.interpolated,
                }
                for p in self.points
            ],
        }


@dataclass
class RallyFeatures:
    """Normalised-ish features that feed the highlight score."""

    duration_s: float
    shot_count: float
    shot_rate: float
    mean_intensity: float
    peak_intensity: float
    coverage: float       # how much of the court width players used
    finish: float         # 0..1, "ended on a bang then stillness"
    serve_onset: float    # 0..1, "started from stillness with a sharp burst"
    net_approach: float   # 0..1, closest approach to the net line
    shot_source: str = "activity"  # "ball" when derived from a ball track

    def to_dict(self) -> dict[str, Any]:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


@dataclass
class Highlight:
    """A scored, categorised rally, ready to be cut."""

    id: str
    segment: Segment
    features: RallyFeatures
    score: float
    categories: list[str]
    contributions: dict[str, float]     # per-feature score contribution, for debugging
    rank: int = 0
    selected: bool = True
    clip_start_s: float = 0.0           # with pre-roll applied
    clip_end_s: float = 0.0
    ball_track: Optional[BallTrack] = None
    clip_path: Optional[str] = None

    @property
    def clip_duration_s(self) -> float:
        return self.clip_end_s - self.clip_start_s

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rank": self.rank,
            "selected": self.selected,
            "score": round(self.score, 4),
            "categories": self.categories,
            "segment": self.segment.to_dict(),
            "clip_start_s": round(self.clip_start_s, 3),
            "clip_end_s": round(self.clip_end_s, 3),
            "clip_duration_s": round(self.clip_duration_s, 3),
            "features": self.features.to_dict(),
            "contributions": {k: round(v, 4) for k, v in self.contributions.items()},
            "ball_track": self.ball_track.to_dict() if self.ball_track else None,
            "clip_path": self.clip_path,
        }

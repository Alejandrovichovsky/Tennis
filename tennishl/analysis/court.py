"""Court geometry, derived from motion rather than from line detection.

We deliberately do *not* run Hough transforms looking for white lines. Court
colour and line contrast vary wildly (clay, hard, indoor, floodlights), while
"where did things move during two hours" is stable on a tripod-mounted phone
and needs no assumptions about the surface.

Outputs:
  * ``roi``   - the rectangle that contains the play. Everything outside is
                spectators, fences, passing traffic.
  * ``net_y`` - the horizontal line separating near player from far player,
                found as the valley between the two modes of player-centroid
                height. Used for the "are both players on court" gate.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import Config
from ..types import FrameObservation


@dataclass
class CourtModel:
    x0: float
    y0: float
    x1: float
    y1: float
    net_y: float
    proxy_size: tuple[int, int]
    confidence: float

    @property
    def width(self) -> float:
        return max(1.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(1.0, self.y1 - self.y0)

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def side(self, feet_y: float) -> int:
        """-1 = far side (top of frame), +1 = near side.

        Decide on the *feet* (bottom of the box), never the centroid: a tall
        near player standing at the net has his centroid above the net line
        while his feet are still on the near court. Feet are geometrically
        unambiguous from a camera behind the baseline.
        """
        return 1 if feet_y >= self.net_y else -1

    def to_dict(self) -> dict:
        return {
            "x0": round(self.x0, 2),
            "y0": round(self.y0, 2),
            "x1": round(self.x1, 2),
            "y1": round(self.y1, 2),
            "net_y": round(self.net_y, 2),
            "proxy_size": list(self.proxy_size),
            "confidence": round(self.confidence, 3),
        }


def _smooth(hist: np.ndarray, k: int = 5) -> np.ndarray:
    if k <= 1 or hist.size < k:
        return hist
    kernel = np.ones(k, dtype=np.float64) / k
    return np.convolve(hist, kernel, mode="same")


def find_net_line(feet_ys: np.ndarray, y0: float, y1: float, bins: int = 48,
                  weights: np.ndarray | None = None) -> tuple[float, float]:
    """Return (net_y, confidence).

    Two players seen from behind the baseline form two modes in the
    histogram of where feet touch the ground. The valley between them is
    the net. ``weights`` lets small (far) blobs count as much as big (near)
    ones even though they are detected less often. If we only see one mode
    (one player, or a doubles pile-up), confidence drops and we fall back to
    the middle of the ROI - which is roughly where a net is anyway.
    """
    fallback = (y0 + y1) / 2.0
    if feet_ys.size < 30:
        return fallback, 0.0

    hist, edges = np.histogram(feet_ys, bins=bins, range=(y0, y1), weights=weights)
    hist = _smooth(hist.astype(np.float64), 5)
    if hist.max() <= 0:
        return fallback, 0.0

    peak_a = int(np.argmax(hist))
    # Second peak: the highest bin that is far enough from the first one that
    # it is a genuinely different mode, not the shoulder of the same one.
    min_sep = max(3, bins // 8)
    mask = np.ones(bins, dtype=bool)
    lo = max(0, peak_a - min_sep)
    hi = min(bins, peak_a + min_sep + 1)
    mask[lo:hi] = False
    if not mask.any() or hist[mask].max() <= 0:
        return fallback, 0.2

    candidates = np.where(mask)[0]
    peak_b = int(candidates[np.argmax(hist[candidates])])
    lo_peak, hi_peak = sorted((peak_a, peak_b))

    # The valley is often a flat stretch of (near-)empty bins - nobody stands
    # on the net. argmin would return its first bin, which sits right next to
    # the far player. Take the middle of the flat minimum instead.
    valley_slice = hist[lo_peak : hi_peak + 1]
    floor = float(valley_slice.min())
    flat = np.where(valley_slice <= floor + 1e-9 + 0.02 * float(valley_slice.max()))[0]
    valley = lo_peak + int(np.median(flat))
    net_y = float((edges[valley] + edges[valley + 1]) / 2.0)

    # Confidence: how deep is the valley compared to the weaker peak?
    weaker = min(hist[lo_peak], hist[hi_peak])
    depth = (weaker - hist[valley]) / weaker if weaker > 0 else 0.0
    return net_y, float(np.clip(depth, 0.0, 1.0))


def estimate_court(
    motion_map: np.ndarray,
    observations: list[FrameObservation],
    proxy_size: tuple[int, int],
    cfg: Config,
) -> CourtModel:
    h, w = motion_map.shape
    total_area = float(w * h)

    # Percentile over pixels that moved at all. Taking it over the whole map
    # would let a mostly-static frame push the threshold onto a plateau and
    # select nothing.
    nonzero = motion_map[motion_map > 0]
    if nonzero.size:
        threshold = max(1e-6, float(np.percentile(nonzero, cfg.court.motion_percentile)))
        hot = motion_map >= threshold
    else:
        hot = np.zeros_like(motion_map, dtype=bool)
    if hot.sum() < 10:
        roi = (0.0, 0.0, float(w), float(h))
        roi_conf = 0.0
    else:
        ys, xs = np.nonzero(hot)
        # Percentile bounds instead of min/max: one cyclist in the background
        # should not stretch the court to the whole frame.
        x0, x1 = np.percentile(xs, [1.0, 99.0])
        y0, y1 = np.percentile(ys, [1.0, 99.0])
        mx = cfg.court.roi_margin_frac * w
        my = cfg.court.roi_margin_frac * h
        roi = (
            float(max(0.0, x0 - mx)),
            float(max(0.0, y0 - my)),
            float(min(w, x1 + mx)),
            float(min(h, y1 + my)),
        )
        area = (roi[2] - roi[0]) * (roi[3] - roi[1])
        roi_conf = float(np.clip(area / total_area / 0.5, 0.0, 1.0))
        if area / total_area < cfg.court.min_roi_area_frac:
            roi = (0.0, 0.0, float(w), float(h))
            roi_conf = 0.0

    x0, y0, x1, y1 = roi
    frame_area = float(w * h)
    feet: list[float] = []
    weights: list[float] = []
    for obs in observations:
        for b in obs.blobs:
            fy = b.cy + b.h / 2.0
            if not (x0 <= b.cx <= x1 and y0 <= fy <= y1 + 0.05 * h):
                continue
            if b.area < cfg.player.min_area_frac_far * frame_area:
                continue
            feet.append(fy)
            # Big blobs (near player, or the near player split into parts)
            # would otherwise drown the far player's mode completely.
            weights.append(1.0 if b.area < cfg.player.max_area_frac_far * frame_area else 0.35)
    net_y, net_conf = find_net_line(
        np.array(feet, dtype=np.float64), y0, y1, weights=np.array(weights, dtype=np.float64)
    )

    return CourtModel(
        x0=x0,
        y0=y0,
        x1=x1,
        y1=y1,
        net_y=net_y,
        proxy_size=proxy_size,
        confidence=float(0.5 * roi_conf + 0.5 * net_conf),
    )

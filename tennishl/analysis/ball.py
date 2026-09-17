"""Ball detection and trajectory linking.

Completely separate from the rest of the pipeline: if this module returns
nothing, highlights still get produced, just without a trail. That is the
intended failure mode - a wrong trail looks broken, a missing trail looks
deliberate.

Why no per-frame ball detector? A tennis ball at 1080p is ~10 px across, and
under motion blur it is a faint streak with the colour of whatever is behind
it. Single-frame appearance is simply not enough information. What *is*
distinctive is its motion: small, fast, and following a smooth, nearly
parabolic path while everything else in the frame is either static or a
person. So we detect motion candidates generously and then let the temporal
linking throw away everything that does not fly like a ball.

Two stages:

1. **Three-frame differencing.** ``min(|f_t - f_{t-1}|, |f_{t+1} - f_t|)``
   keeps only things that moved *into* this position and then *out of* it.
   A static background gives zero; a slowly moving player gives a thin
   outline; a ball gives a compact blob. Then we drop blobs that are too
   large (people), too small (sensor noise), or overlapping a player box.

2. **Trajectory linking with a constant-velocity predictor.** Candidates are
   chained across frames; a chain may skip a few frames (the ball passes in
   front of a dark fence). Chains are scored on length, coverage and how well
   a local quadratic fits - a ball follows physics, a flickering highlight on
   a fence does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import cv2
import numpy as np

from ..config import Config
from ..types import BallPoint, BallTrack, VideoInfo
from ..video.reader import iter_proxy_frames, proxy_scale


@dataclass
class BallCandidate:
    frame: int          # index within the analysed window
    t: float
    x: float            # ball-proxy pixels
    y: float
    area: float
    radius: float
    brightness: float   # 0..1, a weak prior (tennis balls are bright)


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    def contains(self, x: float, y: float, pad: float = 0.0) -> bool:
        return (self.x0 - pad) <= x <= (self.x1 + pad) and (self.y0 - pad) <= y <= (self.y1 + pad)


def detect_candidates(
    prev: np.ndarray,
    cur: np.ndarray,
    nxt: np.ndarray,
    cfg: Config,
    *,
    frame: int = 0,
    t: float = 0.0,
    exclusion_boxes: Sequence[Box] = (),
) -> list[BallCandidate]:
    """Three-frame difference -> small compact moving blobs."""
    d1 = cv2.absdiff(cur, prev)
    d2 = cv2.absdiff(nxt, cur)
    motion = cv2.min(d1, d2)
    _, mask = cv2.threshold(motion, cfg.ball.diff_threshold, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    )

    n, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out: list[BallCandidate] = []
    for i in range(1, n):
        area = float(stats[i, cv2.CC_STAT_AREA])
        if area < cfg.ball.min_area_px or area > cfg.ball.max_area_px:
            continue
        w = float(stats[i, cv2.CC_STAT_WIDTH])
        h = float(stats[i, cv2.CC_STAT_HEIGHT])
        if w <= 0 or h <= 0:
            continue
        aspect = max(w / h, h / w)
        if aspect > 3.5:
            continue  # a long thin streak is a racket edge or a line, not a ball
        bbox_area = w * h
        if bbox_area > 0 and area / bbox_area < 1.0 / cfg.ball.max_fill_ratio:
            continue  # hollow / scattered component

        cx, cy = float(centroids[i][0]), float(centroids[i][1])
        if any(b.contains(cx, cy, cfg.ball.player_exclusion_pad) for b in exclusion_boxes):
            continue

        yi = int(np.clip(cy, 0, cur.shape[0] - 1))
        xi = int(np.clip(cx, 0, cur.shape[1] - 1))
        out.append(
            BallCandidate(
                frame=frame,
                t=t,
                x=cx,
                y=cy,
                area=area,
                radius=float(np.sqrt(area / np.pi)),
                brightness=float(cur[yi, xi]) / 255.0,
            )
        )

    out.sort(key=lambda c: c.brightness, reverse=True)
    return out[: cfg.ball.max_candidates_per_frame]


def _fit_residual(xs: np.ndarray, ys: np.ndarray, ts: np.ndarray) -> float:
    """RMS residual of a local quadratic fit in both axes.

    A flying ball is a parabola in y and close to a straight line in x, so a
    degree-2 fit over a short window should be nearly exact. Anything with a
    large residual is a chain of unrelated blinks.
    """
    if xs.size < 4:
        return 0.0
    residuals: list[float] = []
    win = 5
    for i in range(0, max(1, xs.size - win + 1)):
        tw = ts[i : i + win]
        if tw.size < 4:
            break
        tw = tw - tw[0]
        for vals in (xs[i : i + win], ys[i : i + win]):
            coef = np.polyfit(tw, vals, 2)
            pred = np.polyval(coef, tw)
            residuals.append(float(np.sqrt(np.mean((vals - pred) ** 2))))
    return float(np.mean(residuals)) if residuals else 0.0


def link_tracks(
    candidates_per_frame: Sequence[Sequence[BallCandidate]],
    cfg: Config,
) -> list[tuple[list[BallCandidate], float, float]]:
    """Chain candidates into trajectories.

    Returns ``(points, confidence, rms_residual)`` per track, best first.
    Pure Python/numpy so it can be unit-tested against synthetic parabolas.
    """
    used: set[tuple[int, int]] = set()
    tracks: list[tuple[list[BallCandidate], float, float]] = []
    n_frames = len(candidates_per_frame)

    for f0 in range(n_frames):
        for i0, seed in enumerate(candidates_per_frame[f0]):
            if (f0, i0) in used:
                continue

            chain: list[tuple[int, int, BallCandidate]] = [(f0, i0, seed)]
            velocity: tuple[float, float] | None = None
            last_frame = f0
            last = seed

            f = f0 + 1
            while f < n_frames:
                gap = f - last_frame
                if gap > cfg.ball.max_gap_frames + 1:
                    break

                if velocity is None:
                    px, py = last.x, last.y
                    gate = cfg.ball.max_speed_px_per_frame * gap
                else:
                    px = last.x + velocity[0] * gap
                    py = last.y + velocity[1] * gap
                    # Tighter gate once we know where the ball should be: it
                    # may accelerate (gravity, a stroke) but not teleport.
                    gate = 0.45 * cfg.ball.max_speed_px_per_frame * gap + 6.0

                best_idx = -1
                best_dist = gate
                for j, cand in enumerate(candidates_per_frame[f]):
                    if (f, j) in used:
                        continue
                    d = float(np.hypot(cand.x - px, cand.y - py))
                    if d < best_dist:
                        best_dist = d
                        best_idx = j

                if best_idx >= 0:
                    cand = candidates_per_frame[f][best_idx]
                    step = max(1, f - last_frame)
                    velocity = ((cand.x - last.x) / step, (cand.y - last.y) / step)
                    chain.append((f, best_idx, cand))
                    last = cand
                    last_frame = f
                f += 1

            if len(chain) < cfg.ball.min_track_points:
                continue

            points = [c for _, _, c in chain]
            ts = np.array([c.t for c in points])
            xs = np.array([c.x for c in points])
            ys = np.array([c.y for c in points])
            rms = _fit_residual(xs, ys, ts)

            span = max(1, chain[-1][0] - chain[0][0] + 1)
            len_score = min(1.0, len(points) / float(cfg.ball.full_length_points))
            cover_score = len(points) / span
            smooth_score = float(np.exp(-rms / max(1e-6, cfg.ball.accel_tolerance_px)))
            # Smoothness dominates on purpose. A chain crawling along a
            # player's body is long and dense but jagged; a ball in flight
            # is the one thing on court that follows a clean parabola.
            confidence = float(
                np.clip(0.25 * len_score + 0.55 * smooth_score + 0.20 * cover_score, 0.0, 1.0)
            )

            for f_idx, c_idx, _ in chain:
                used.add((f_idx, c_idx))
            tracks.append((points, confidence, rms))

    tracks.sort(key=lambda tr: (tr[1], len(tr[0])), reverse=True)
    return tracks


def _to_track(
    points: Sequence[BallCandidate],
    confidence: float,
    rms: float,
    proxy_w: int,
    proxy_h: int,
    fps: float,
) -> BallTrack:
    """Normalise coordinates and fill short gaps so the trail draws smoothly."""
    out: list[BallPoint] = []
    for a, b in zip(points, list(points[1:]) + [None]):
        out.append(
            BallPoint(t=a.t, x=a.x / proxy_w, y=a.y / proxy_h, radius_px=a.radius, interpolated=False)
        )
        if b is None:
            continue
        gap_frames = int(round((b.t - a.t) * fps)) - 1
        for k in range(1, max(0, gap_frames) + 1):
            f = k / (gap_frames + 1)
            out.append(
                BallPoint(
                    t=a.t + (b.t - a.t) * f,
                    x=(a.x + (b.x - a.x) * f) / proxy_w,
                    y=(a.y + (b.y - a.y) * f) / proxy_h,
                    radius_px=a.radius,
                    interpolated=True,
                )
            )
    return BallTrack(points=out, confidence=confidence, rms_residual_px=rms)


def track_ball_in_window(
    info: VideoInfo,
    start_s: float,
    end_s: float,
    cfg: Config,
    *,
    exclusion_boxes_by_time: Iterable[tuple[float, list[Box]]] = (),
) -> BallTrack | None:
    """Run the full ball pass over one rally window. Returns the best track.

    ``exclusion_boxes_by_time`` are player boxes from the coarse pass, already
    scaled to ball-proxy pixels, so we can ignore arms and rackets.
    """
    boxes_timeline = sorted(exclusion_boxes_by_time, key=lambda item: item[0])
    box_times = np.array([bt for bt, _ in boxes_timeline]) if boxes_timeline else np.zeros(0)

    def boxes_at(t: float) -> list[Box]:
        if box_times.size == 0:
            return []
        idx = int(np.clip(np.searchsorted(box_times, t), 0, box_times.size - 1))
        return boxes_timeline[idx][1]

    frames: list[np.ndarray] = []
    times: list[float] = []
    for pf in iter_proxy_frames(
        info,
        target_width=cfg.proxy.ball_width,
        target_fps=cfg.proxy.ball_fps,
        start_s=start_s,
        end_s=end_s,
        grayscale=True,
    ):
        frames.append(cv2.GaussianBlur(pf.image, (3, 3), 0))
        times.append(pf.t)

    if len(frames) < cfg.ball.min_track_points + 2:
        return None

    scale = proxy_scale(info, cfg.proxy.ball_width)
    proxy_w = max(2, int(round(info.width * scale)))
    proxy_h = max(2, int(round(info.height * scale)))

    candidates_per_frame: list[list[BallCandidate]] = [[]]
    for i in range(1, len(frames) - 1):
        candidates_per_frame.append(
            detect_candidates(
                frames[i - 1],
                frames[i],
                frames[i + 1],
                cfg,
                frame=i,
                t=times[i],
                exclusion_boxes=boxes_at(times[i]),
            )
        )
    candidates_per_frame.append([])

    tracks = link_tracks(candidates_per_frame, cfg)
    if not tracks:
        return None
    return stitch_tracks(tracks, cfg, proxy_w=proxy_w, proxy_h=proxy_h, fps=info.fps)


def stitch_tracks(
    tracks: Sequence[tuple[list[BallCandidate], float, float]],
    cfg: Config,
    *,
    proxy_w: int,
    proxy_h: int,
    fps: float,
) -> BallTrack | None:
    """Combine every trusted chain in the window into one track.

    A rally is many flights of the ball, and each flight tends to become its
    own chain (the chain breaks where the ball meets a racket and gets
    excluded by the player box). Keeping only the single best chain would
    draw a trail for one shot and nothing for the rest. So: take all chains
    above the confidence floor, order them in time, drop any that overlap a
    better one, and concatenate. Gaps stay gaps - we never interpolate across
    a chain boundary, so the trail simply disappears while we are unsure.

    If nothing clears the floor, return the best single chain anyway, with
    its (low) confidence, so the report can say *why* there is no trail.
    """
    trusted = [tr for tr in tracks if tr[1] >= cfg.ball.min_confidence]
    if not trusted:
        points, confidence, rms = tracks[0]
        return _to_track(points, confidence, rms, proxy_w, proxy_h, fps)

    trusted.sort(key=lambda tr: tr[0][0].t)
    kept: list[tuple[list[BallCandidate], float, float]] = []
    for points, confidence, rms in trusted:
        if kept:
            last_t = kept[-1][0][-1].t
            # A chain may have grown a short tail into the next flight. Trim
            # the overlap off the later chain instead of throwing it away.
            points = [p for p in points if p.t > last_t]
            if len(points) < max(3, cfg.ball.min_track_points // 2):
                continue
        kept.append((points, confidence, rms))

    all_points: list[BallPoint] = []
    total_n = 0
    conf_sum = 0.0
    rms_sum = 0.0
    for points, confidence, rms in kept:
        part = _to_track(points, confidence, rms, proxy_w, proxy_h, fps)
        all_points.extend(part.points)
        total_n += len(points)
        conf_sum += confidence * len(points)
        rms_sum += rms * len(points)

    return BallTrack(
        points=all_points,
        confidence=conf_sum / max(1, total_n),
        rms_residual_px=rms_sum / max(1, total_n),
    )

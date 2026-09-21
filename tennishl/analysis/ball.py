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

from dataclasses import dataclass, replace
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
    on_player: bool = False  # overlaps a player box - kept, but not trusted


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
    """Three-frame difference -> small compact moving blobs.

    Candidates overlapping a player box are flagged, not dropped. Dropping
    them costs two thirds of all detections on real footage, because the
    ball spends much of a rally in front of, behind or beside a player as
    seen from the camera. The flag lets ``link_tracks`` refuse to *start* a
    chain on a body while still letting the ball fly past one.
    """
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
        on_player = any(b.contains(cx, cy, cfg.ball.player_exclusion_pad) for b in exclusion_boxes)

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
                on_player=on_player,
            )
        )

    # Free candidates first: a body produces dozens per frame and would
    # otherwise push the actual ball out of the per-frame cap.
    out.sort(key=lambda c: (c.on_player, -c.brightness))
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

    Two rules keep bodies out without throwing the ball away with them:

    * a chain may never *start* on a player - a body yields dozens of
      candidates per frame and would seed long, dense, useless chains;
    * the local quadratic residual is a hard gate, not a soft weight. On
      real footage a flying ball fits to 0.1-0.3 px while a chain crawling
      along a torso sits at 1-6 px. A whole order of magnitude, so a fixed
      ceiling separates them cleanly.
    """
    used: set[tuple[int, int]] = set()
    tracks: list[tuple[list[BallCandidate], float, float]] = []
    n_frames = len(candidates_per_frame)

    for f0 in range(n_frames):
        for i0, seed in enumerate(candidates_per_frame[f0]):
            if (f0, i0) in used or seed.on_player:
                continue
            # A seed is spent whether or not its chain survives, so one
            # rejected chain cannot make us retry from the same point.
            used.add((f0, i0))

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
            on_player_frac = sum(1 for c in points if c.on_player) / len(points)
            if on_player_frac > cfg.ball.max_on_player_frac:
                continue

            ts = np.array([c.t for c in points])
            xs = np.array([c.x for c in points])
            ys = np.array([c.y for c in points])
            rms = _fit_residual(xs, ys, ts)
            if rms > cfg.ball.max_rms_px:
                continue

            span = max(1, chain[-1][0] - chain[0][0] + 1)
            len_score = min(1.0, len(points) / float(cfg.ball.full_length_points))
            cover_score = len(points) / span
            smooth_score = float(np.exp(-rms / max(1e-6, cfg.ball.accel_tolerance_px)))
            # Everything here already passed the residual ceiling, so
            # smoothness no longer has to carry the boll-vs-body decision
            # and length can say more about how much of a flight we caught.
            confidence = float(
                np.clip(0.40 * len_score + 0.35 * smooth_score + 0.25 * cover_score, 0.0, 1.0)
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


def scaled_config(cfg: Config, proxy_w: int) -> Config:
    """Rescale the pixel-valued ball thresholds to the proxy actually used.

    Areas scale with the square of the linear factor, everything else
    linearly. This is what lets ``proxy.ball_width`` be tuned freely without
    touching any other number.
    """
    s = proxy_w / float(cfg.ball.reference_width)
    if abs(s - 1.0) < 1e-6:
        return cfg
    ball = replace(
        cfg.ball,
        min_area_px=cfg.ball.min_area_px * s * s,
        max_area_px=cfg.ball.max_area_px * s * s,
        player_exclusion_pad=int(round(cfg.ball.player_exclusion_pad * s)),
        max_speed_px_per_frame=cfg.ball.max_speed_px_per_frame * s,
        accel_tolerance_px=cfg.ball.accel_tolerance_px * s,
        max_rms_px=cfg.ball.max_rms_px * s,
        join_tolerance_px=cfg.ball.join_tolerance_px * s,
    )
    return replace(cfg, ball=ball)


def track_ball_in_window(
    info: VideoInfo,
    start_s: float,
    end_s: float,
    cfg: Config,
    *,
    exclusion_boxes_by_time: Iterable[tuple[float, list[Box]]] = (),
    audio_hits: np.ndarray | None = None,
) -> BallTrack | None:
    """Run the full ball pass over one rally window. Returns the best track.

    ``exclusion_boxes_by_time`` are player boxes from the coarse pass, already
    scaled to ball-proxy pixels, so we can tell candidates on a body apart.
    ``audio_hits`` are racket-impact times; they decide which gaps in the
    trail are real direction changes and which are tracking failures.
    """
    boxes_timeline = sorted(exclusion_boxes_by_time, key=lambda item: item[0])
    box_times = np.array([bt for bt, _ in boxes_timeline]) if boxes_timeline else np.zeros(0)

    def boxes_at(t: float) -> list[Box]:
        if box_times.size == 0:
            return []
        idx = int(np.clip(np.searchsorted(box_times, t), 0, box_times.size - 1))
        return boxes_timeline[idx][1]

    scale = proxy_scale(info, cfg.proxy.ball_width)
    proxy_w = max(2, int(round(info.width * scale)))
    proxy_h = max(2, int(round(info.height * scale)))
    cfg = scaled_config(cfg, proxy_w)

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
    return stitch_tracks(
        tracks, cfg, proxy_w=proxy_w, proxy_h=proxy_h, fps=info.fps, audio_hits=audio_hits
    )


def _can_join(
    left: list[BallCandidate],
    right: list[BallCandidate],
    cfg: Config,
    audio_hits: np.ndarray | None,
) -> bool:
    """Are these two chains the same flight, briefly lost?

    The physics does most of the work: extrapolate the first chain's
    parabola across the hole, and only join if the ball reappears where it
    should. Anything that struck the ball in between would have changed its
    path far more than the tolerance allows, so this test stands on its own
    and the bridging works on silent footage too.

    Audio is then a second, independent veto: if a racket was heard inside
    the gap, that is a real boundary between two flights no matter how well
    the prediction happens to line up.
    """
    a, b = left[-1], right[0]
    gap_s = b.t - a.t
    if gap_s <= 0 or gap_s > cfg.ball.join_max_gap_s:
        return False

    if audio_hits is not None and audio_hits.size:
        lo = np.searchsorted(audio_hits, a.t - cfg.ball.join_hit_margin_s, side="left")
        hi = np.searchsorted(audio_hits, b.t + cfg.ball.join_hit_margin_s, side="right")
        if hi > lo:
            return False  # something was struck in between: a real boundary

    if len(left) < 2:
        return False
    px, py = _extrapolate(left, b.t)
    return float(np.hypot(b.x - px, b.y - py)) <= cfg.ball.join_tolerance_px


def _extrapolate(points: Sequence[BallCandidate], t_target: float) -> tuple[float, float]:
    """Where the chain says the ball should be at ``t_target``.

    Quadratic, not linear: gravity bends the path enough that a straight
    extrapolation over a 0.3 s hole is off by ~100 px at 1080p, which would
    reject every join we actually want to make.
    """
    tail = list(points[-min(len(points), 7):])
    ts = np.array([p.t for p in tail], dtype=np.float64)
    t0 = ts[0]
    ts = ts - t0
    deg = 2 if len(tail) >= 4 else 1
    cx = np.polyfit(ts, np.array([p.x for p in tail]), deg)
    cy = np.polyfit(ts, np.array([p.y for p in tail]), deg)
    dt = t_target - t0
    return float(np.polyval(cx, dt)), float(np.polyval(cy, dt))


def stitch_tracks(
    tracks: Sequence[tuple[list[BallCandidate], float, float]],
    cfg: Config,
    *,
    proxy_w: int,
    proxy_h: int,
    fps: float,
    audio_hits: np.ndarray | None = None,
) -> BallTrack | None:
    """Combine every trusted chain in the window into one track.

    A rally is many flights of the ball, and each flight tends to become its
    own chain. Keeping only the single best chain would draw a trail for one
    shot and nothing for the rest. So: take all chains above the confidence
    floor, order them in time, drop any that overlap a better one, and
    concatenate.

    Gaps are then classified rather than blanket-preserved: a hole the
    ball's own parabola can explain is a tracking failure we interpolate
    across, anything else stays a gap and the trail simply disappears while
    we are unsure. When the file has audio, a racket heard inside a gap
    vetoes the join as well.

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

    hits = None if audio_hits is None else np.sort(np.asarray(audio_hits, dtype=np.float64))
    all_points: list[BallPoint] = []
    total_n = 0
    conf_sum = 0.0
    rms_sum = 0.0
    prev_points: list[BallCandidate] | None = None

    for points, confidence, rms in kept:
        part = _to_track(points, confidence, rms, proxy_w, proxy_h, fps)
        if prev_points is not None and _can_join(prev_points, points, cfg, hits):
            # Same flight: fill the hole so the trail stays continuous.
            a, b = prev_points[-1], points[0]
            n_fill = max(0, int(round((b.t - a.t) * fps)) - 1)
            for k in range(1, n_fill + 1):
                f = k / (n_fill + 1)
                all_points.append(
                    BallPoint(
                        t=a.t + (b.t - a.t) * f,
                        x=(a.x + (b.x - a.x) * f) / proxy_w,
                        y=(a.y + (b.y - a.y) * f) / proxy_h,
                        radius_px=a.radius,
                        interpolated=True,
                    )
                )
        all_points.extend(part.points)
        total_n += len(points)
        conf_sum += confidence * len(points)
        rms_sum += rms * len(points)
        prev_points = points

    return BallTrack(
        points=all_points,
        confidence=conf_sum / max(1, total_n),
        rms_residual_px=rms_sum / max(1, total_n),
    )

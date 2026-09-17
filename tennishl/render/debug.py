"""Debug visualisations for tuning heuristics on real footage.

``write_signal_plot`` draws the activity signal, both thresholds and the
accepted/rejected segments as a PNG - the first thing to look at when a cut
is wrong. ``write_debug_video`` re-runs the proxy decode and overlays what
the analysis saw on each frame (ROI, net line, player blobs, a(t)).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..analysis.activity import ActivitySignal, select_players
from ..analysis.court import CourtModel
from ..analysis.segmentation import SegmentationResult
from ..config import Config
from ..types import FrameObservation, VideoInfo
from ..video.reader import iter_proxy_frames


def write_signal_plot(out_path: Path, signal: ActivitySignal, seg: SegmentationResult, *, width: int = 1800, height: int = 420) -> None:
    img = np.full((height, width, 3), 24, dtype=np.uint8)
    n = len(signal)
    if n < 2:
        cv2.imwrite(str(out_path), img)
        return

    t0, t1 = float(signal.t[0]), float(signal.t[-1])
    pad_l, pad_b, pad_t = 50, 40, 20

    def x_of(t: float) -> int:
        return int(pad_l + (t - t0) / max(1e-6, t1 - t0) * (width - pad_l - 10))

    def y_of(v: float) -> int:
        return int(height - pad_b - float(np.clip(v, 0, 1)) * (height - pad_b - pad_t))

    for s in seg.segments:
        cv2.rectangle(img, (x_of(s.start_s), pad_t), (x_of(s.end_s), height - pad_b), (40, 90, 40), -1)
    for r in seg.rejected:
        cv2.rectangle(img, (x_of(r.start_s), pad_t), (x_of(r.end_s), height - pad_b), (40, 40, 90), -1)

    def polyline(values: np.ndarray, colour: tuple[int, int, int], thickness: int = 1) -> None:
        pts = np.array([[x_of(float(t)), y_of(float(v))] for t, v in zip(signal.t, values)], np.int32)
        cv2.polylines(img, [pts], False, colour, thickness, cv2.LINE_AA)

    polyline(signal.fg, (120, 120, 120))
    polyline(signal.speed, (200, 120, 60))
    if signal.audio is not None:
        polyline(signal.audio, (220, 80, 220))
    if signal.audio_hits is not None:
        for ht in signal.audio_hits:
            xh = x_of(float(ht))
            cv2.line(img, (xh, height - pad_b - 8), (xh, height - pad_b), (220, 80, 220), 1)
    polyline(signal.smoothed, (60, 220, 255), 2)
    cv2.line(img, (pad_l, y_of(seg.enter_threshold)), (width - 10, y_of(seg.enter_threshold)), (80, 200, 80), 1)
    cv2.line(img, (pad_l, y_of(seg.exit_threshold)), (width - 10, y_of(seg.exit_threshold)), (80, 80, 200), 1)

    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(img, "a(t)", (8, y_of(0.9)), font, 0.45, (60, 220, 255), 1, cv2.LINE_AA)
    cv2.putText(img, "speed", (8, y_of(0.8)), font, 0.45, (200, 120, 60), 1, cv2.LINE_AA)
    cv2.putText(img, "fg", (8, y_of(0.7)), font, 0.45, (120, 120, 120), 1, cv2.LINE_AA)
    if signal.audio is not None:
        cv2.putText(img, "audio", (8, y_of(0.6)), font, 0.45, (220, 80, 220), 1, cv2.LINE_AA)
    for m in range(0, int(t1) + 1, 60):
        x = x_of(float(m))
        cv2.line(img, (x, height - pad_b), (x, height - pad_b + 6), (140, 140, 140), 1)
        cv2.putText(img, f"{m // 60}:00", (x - 12, height - 12), font, 0.4, (160, 160, 160), 1, cv2.LINE_AA)
    cv2.imwrite(str(out_path), img)


def write_debug_video(
    out_path: Path,
    info: VideoInfo,
    observations: list[FrameObservation],
    court: CourtModel,
    seg: SegmentationResult,
    cfg: Config,
    *,
    max_seconds: float | None = None,
) -> None:
    by_frame = {o.frame_index: o for o in observations}
    pw, ph = court.proxy_size
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), cfg.proxy.coarse_fps, (pw, ph))
    active_ranges = [(s.start_s, s.end_s) for s in seg.segments]
    font = cv2.FONT_HERSHEY_SIMPLEX

    for pf in iter_proxy_frames(info, target_width=cfg.proxy.coarse_width, target_fps=cfg.proxy.coarse_fps, end_s=max_seconds):
        frame = pf.image.copy()
        obs = by_frame.get(pf.frame_index)
        cv2.rectangle(frame, (int(court.x0), int(court.y0)), (int(court.x1), int(court.y1)), (200, 200, 60), 1)
        cv2.line(frame, (int(court.x0), int(court.net_y)), (int(court.x1), int(court.net_y)), (60, 200, 200), 1)
        if obs is not None:
            near, far = select_players(obs.blobs, court, cfg)
            for b, colour in ((near, (60, 60, 255)), (far, (255, 120, 60))):
                if b is None:
                    continue
                x, y, w, h = b.box
                cv2.rectangle(frame, (int(x), int(y)), (int(x + w), int(y + h)), colour, 2)
            active = any(s <= pf.t <= e for s, e in active_ranges)
            bar_w = int(obs.activity * (pw - 20))
            cv2.rectangle(frame, (10, ph - 16), (10 + bar_w, ph - 6), (60, 220, 255) if active else (120, 120, 120), -1)
            cv2.putText(
                frame,
                f"t={pf.t:7.2f}  a={obs.activity:.2f} spd={obs.player_speed:.2f} spread={int(obs.spread)} {'RALLY' if active else ''}",
                (10, 18), font, 0.45, (255, 255, 255), 1, cv2.LINE_AA,
            )
        writer.write(frame)
    writer.release()

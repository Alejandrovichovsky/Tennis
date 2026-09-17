"""Synthetic tennis-ish video for tests and demos.

Not a simulation of tennis - a simulation of what the *pipeline* sees from a
tripod behind the baseline: a static court, two person-sized blobs that move
fast during points and slowly between them, a small bright ball that flies
between them in arcs, sensor noise, and (optionally) a camera bump.

Returns the ground-truth point intervals so tests can score the segmenter.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class Point:
    start_s: float
    end_s: float
    shots: int


@dataclass
class SynthSpec:
    width: int = 1280
    height: int = 720
    fps: float = 30.0
    seconds: float = 90.0
    seed: int = 7
    camera_bump_at_s: float | None = None
    noise_sigma: float = 3.0


def _court_background(w: int, h: int, rng: random.Random) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (70, 90, 60)  # surroundings, dull green-grey
    # Court trapezoid (perspective from behind the baseline).
    top_y, bot_y = int(h * 0.18), int(h * 0.95)
    top_x0, top_x1 = int(w * 0.36), int(w * 0.64)
    bot_x0, bot_x1 = int(w * 0.12), int(w * 0.88)
    court = np.array([[top_x0, top_y], [top_x1, top_y], [bot_x1, bot_y], [bot_x0, bot_y]], np.int32)
    cv2.fillPoly(img, [court], (40, 120, 200))  # clay-ish orange in BGR
    white = (235, 235, 235)
    cv2.polylines(img, [court], True, white, 2)
    net_y = int(h * 0.5)
    nx0 = int(np.interp(net_y, [top_y, bot_y], [top_x0, bot_x0]))
    nx1 = int(np.interp(net_y, [top_y, bot_y], [top_x1, bot_x1]))
    cv2.line(img, (nx0, net_y), (nx1, net_y), (200, 200, 200), 3)
    # service lines
    for fy in (0.34, 0.68):
        y = int(np.interp(fy, [0.18, 0.95], [top_y, bot_y]))
        x0 = int(np.interp(y, [top_y, bot_y], [top_x0, bot_x0]))
        x1 = int(np.interp(y, [top_y, bot_y], [top_x1, bot_x1]))
        cv2.line(img, (x0, y), (x1, y), white, 2)
    # a fence and some static clutter
    cv2.rectangle(img, (0, 0), (w, int(h * 0.12)), (60, 60, 60), -1)
    for _ in range(40):
        x, y = rng.randrange(w), rng.randrange(int(h * 0.12))
        cv2.circle(img, (x, y), rng.randrange(2, 6), (90, 90, 90), -1)
    return img


def _schedule(spec: SynthSpec, rng: random.Random) -> list[Point]:
    points: list[Point] = []
    t = 6.0  # opening dead time
    while t < spec.seconds - 6.0:
        shots = rng.choice([2, 3, 4, 6, 8, 11, 14])
        duration = 1.2 + shots * rng.uniform(0.75, 1.05)
        if t + duration > spec.seconds - 4.0:
            break
        points.append(Point(start_s=t, end_s=t + duration, shots=shots))
        t += duration + rng.uniform(5.0, 12.0)
    return points


def generate(out_path: str | Path, spec: SynthSpec | None = None) -> list[Point]:
    spec = spec or SynthSpec()
    rng = random.Random(spec.seed)
    np_rng = np.random.default_rng(spec.seed)
    w, h = spec.width, spec.height
    bg = _court_background(w, h, rng)
    points = _schedule(spec, rng)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, spec.fps, (w, h))
    if not writer.isOpened():
        raise IOError(f"Could not open VideoWriter for {out_path}")

    n_frames = int(spec.seconds * spec.fps)
    near_base_y, far_base_y = int(h * 0.90), int(h * 0.24)

    # Sensor noise: drawing fresh Gaussian noise per 1280x720x3 frame is the
    # single slowest thing in this generator, so pre-bake a small bank of
    # noise frames and cycle through them with a random roll.
    noise_bank = [
        np_rng.normal(0.0, spec.noise_sigma, size=(h, w, 3)).astype(np.int16) for _ in range(6)
    ]
    near_size, far_size = (int(w * 0.045), int(h * 0.20)), (int(w * 0.025), int(h * 0.11))

    # Idle positions drift slowly so dead time is not perfectly static.
    near_x, far_x = w * 0.5, w * 0.5
    idle_target_near, idle_target_far = w * 0.5, w * 0.5
    idle_changed_at = -10.0

    bump_offset = (0, 0)

    for fi in range(n_frames):
        t = fi / spec.fps
        frame = bg.copy()

        active = next((p for p in points if p.start_s <= t <= p.end_s), None)
        ball = None

        if active is not None:
            phase = (t - active.start_s) / (active.end_s - active.start_s)
            # Fast lateral movement with direction changes (shot-like bursts).
            k = active.shots
            near_x = w * 0.5 + w * 0.22 * math.sin(2 * math.pi * k * 0.5 * phase + 0.3)
            far_x = w * 0.5 + w * 0.12 * math.sin(2 * math.pi * k * 0.5 * phase + 2.1)
            near_y_off = 12 * math.sin(2 * math.pi * k * phase)
            far_y_off = 6 * math.sin(2 * math.pi * k * phase + 1.0)

            # Ball: alternates between players once per shot, arcing in x.
            shot_phase = (phase * k) % 1.0
            shot_idx = int(phase * k)
            going_far = shot_idx % 2 == 0
            y0, y1 = (near_base_y - near_size[1] // 2, far_base_y) if going_far else (far_base_y, near_base_y - near_size[1] // 2)
            x0 = near_x if going_far else far_x
            x1 = far_x if going_far else near_x
            by = y0 + (y1 - y0) * shot_phase
            bx = x0 + (x1 - x0) * shot_phase + 40 * math.sin(math.pi * shot_phase)
            ball = (int(bx), int(by))
        else:
            if t - idle_changed_at > 4.0:
                idle_target_near = w * rng.uniform(0.3, 0.7)
                idle_target_far = w * rng.uniform(0.35, 0.65)
                idle_changed_at = t
            # walk slowly toward the idle target
            near_x += (idle_target_near - near_x) * 0.01
            far_x += (idle_target_far - far_x) * 0.01
            near_y_off, far_y_off = 0.0, 0.0

        # Draw players as two-tone blobs (body + head), near one larger.
        for (x, base_y, size, y_off, colour) in (
            (near_x, near_base_y, near_size, near_y_off, (40, 40, 200)),
            (far_x, far_base_y, far_size, far_y_off, (200, 60, 40)),
        ):
            bw, bh = size
            x0i, y0i = int(x - bw / 2), int(base_y - bh + y_off)
            cv2.rectangle(frame, (x0i, y0i), (x0i + bw, int(base_y + y_off)), colour, -1)
            cv2.circle(frame, (int(x), y0i - bw // 3), bw // 3, (180, 150, 130), -1)

        if ball is not None:
            r = 6 if ball[1] > h * 0.5 else 4
            cv2.circle(frame, ball, r, (80, 240, 255), -1, cv2.LINE_AA)

        if spec.camera_bump_at_s is not None and abs(t - spec.camera_bump_at_s) < 0.35:
            bump_offset = (rng.randint(-25, 25), rng.randint(-15, 15))
            M = np.float32([[1, 0, bump_offset[0]], [0, 1, bump_offset[1]]])
            frame = cv2.warpAffine(frame, M, (w, h), borderMode=cv2.BORDER_REPLICATE)

        noise = noise_bank[fi % len(noise_bank)]
        noise = np.roll(noise, shift=(fi * 37) % h, axis=0)
        frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        writer.write(frame)

    writer.release()
    return points


def write_ground_truth(points: list[Point], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps([{"start_s": p.start_s, "end_s": p.end_s, "shots": p.shots} for p in points], indent=2),
        encoding="utf-8",
    )

"""Court model and activity signal on hand-built observations."""

from __future__ import annotations

import numpy as np

from tennishl.config import Config
from tennishl.analysis.activity import compute_activity, select_players
from tennishl.analysis.court import CourtModel, estimate_court, find_net_line
from tennishl.types import Blob, FrameObservation


def test_net_line_sits_in_the_valley_between_two_players():
    rng = np.random.default_rng(0)
    near = rng.normal(220, 6, 400)
    far = rng.normal(60, 4, 400)
    net_y, conf = find_net_line(np.concatenate([near, far]), 0.0, 270.0)
    assert 100 < net_y < 180
    assert conf > 0.8


def test_net_line_falls_back_with_one_player():
    ys = np.random.default_rng(1).normal(200, 5, 300)
    net_y, conf = find_net_line(ys, 0.0, 270.0)
    assert conf < 0.5
    assert 0 <= net_y <= 270


def make_observations(n: int, dt: float, *, near_speed_px: float, far_speed_px: float,
                      court_h: float = 270.0) -> list[FrameObservation]:
    obs = []
    for i in range(n):
        t = i * dt
        near_x = 240 + 80 * np.sin(2 * np.pi * 0.7 * t) * (near_speed_px > 0)
        far_x = 240 + 40 * np.sin(2 * np.pi * 0.7 * t + 1) * (far_speed_px > 0)
        blobs = [
            Blob(cx=near_x, cy=220, w=20, h=60, area=1000),
            Blob(cx=far_x, cy=60, w=10, h=30, area=250),
        ]
        obs.append(FrameObservation(index=i, frame_index=i * 3, t=t, fg_ratio=0.01 + 0.02 * (near_speed_px > 0),
                                    camera_motion=0.0, blobs=blobs))
    return obs


def test_select_players_one_per_side():
    court = CourtModel(x0=0, y0=0, x1=480, y1=270, net_y=130, proxy_size=(480, 270), confidence=1.0)
    blobs = [Blob(100, 200, 20, 60, 900), Blob(300, 210, 25, 70, 1500), Blob(240, 50, 10, 30, 200),
             Blob(470, 265, 5, 5, 25)]
    near, far = select_players(blobs, court)
    assert near is not None and near.area == 1500
    assert far is not None and far.cy == 50


def test_activity_is_higher_when_players_move():
    cfg = Config()
    court = CourtModel(x0=0, y0=0, x1=480, y1=270, net_y=130, proxy_size=(480, 270), confidence=1.0)
    moving = make_observations(200, 0.1, near_speed_px=5, far_speed_px=3)
    still = make_observations(200, 0.1, near_speed_px=0, far_speed_px=0)
    both = still[:100] + [FrameObservation(index=100 + o.index, frame_index=o.frame_index, t=10 + o.t,
                                           fg_ratio=o.fg_ratio, camera_motion=0.0, blobs=o.blobs)
                          for o in moving[:100]]
    sig = compute_activity(both, court, cfg)
    assert sig.smoothed[:80].mean() < 0.35
    assert sig.smoothed[120:].mean() > 0.6
    assert np.all(sig.spread == 1.0)


def test_estimate_court_from_motion_map():
    cfg = Config()
    motion = np.zeros((270, 480), dtype=np.float32)
    motion[40:250, 100:380] = 5.0   # everything moved inside this box
    motion[10, 10] = 100.0           # one hot pixel far away (a bird)
    obs = make_observations(50, 0.1, near_speed_px=5, far_speed_px=3)
    court = estimate_court(motion, obs, (480, 270), cfg)
    assert 60 < court.x0 < 110 and 370 < court.x1 < 420
    assert court.y0 < 45 and court.y1 > 245
    assert 80 < court.net_y < 200

"""Ball-hit detection on synthetic audio: clicks in noise, plus wind."""

from __future__ import annotations

import numpy as np

from tennishl.analysis.audio import SAMPLE_RATE, hit_density, hits_between, onset_strength, pick_hits


def synth_audio(seconds: float, hit_times: list[float], *, noise: float = 0.01, wind: bool = False, seed: int = 0):
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE)
    x = rng.normal(0, noise, n).astype(np.float32)
    if wind:
        # Slow low-frequency rumble, much louder than the hits' band energy.
        t = np.arange(n) / SAMPLE_RATE
        x += (0.3 * np.sin(2 * np.pi * 60 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.3 * t))).astype(np.float32)
    for ht in hit_times:
        i = int(ht * SAMPLE_RATE)
        length = int(0.008 * SAMPLE_RATE)   # 8 ms broadband burst with fast decay
        burst = rng.normal(0, 0.4, length) * np.exp(-np.arange(length) / (length / 4))
        x[i : i + length] += burst.astype(np.float32)
    return x


def test_hits_are_found_at_the_right_times():
    truth = [1.0, 1.9, 2.7, 3.6, 8.0, 8.8, 9.5]
    x = synth_audio(12.0, truth)
    t, onset = onset_strength(x)
    hits, thr = pick_hits(t, onset)
    assert len(hits) == len(truth), hits
    assert np.all(np.abs(np.array(hits) - np.array(truth)) < 0.03)


def test_wind_does_not_trigger():
    x = synth_audio(12.0, [], wind=True)
    t, onset = onset_strength(x)
    hits, _ = pick_hits(t, onset)
    assert len(hits) == 0


def test_hits_survive_wind():
    truth = [2.0, 2.9, 3.7]
    x = synth_audio(8.0, truth, wind=True)
    t, onset = onset_strength(x)
    hits, _ = pick_hits(t, onset)
    assert len(hits) == 3
    assert np.all(np.abs(np.array(hits) - np.array(truth)) < 0.03)


def test_density_and_counting():
    hits = np.array([10.0, 10.8, 11.6, 12.5, 30.0])
    t = np.array([5.0, 11.0, 12.0, 30.0, 40.0])
    d = hit_density(hits, t, window_s=1.5)
    assert d[0] == 0.0
    assert d[1] > d[3] > 0.0
    assert d[4] == 0.0
    assert hits_between(hits, 9.0, 13.0) == 4
    assert hits_between(hits, 20.0, 25.0) == 0

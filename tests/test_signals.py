from __future__ import annotations

import numpy as np

from tennishl.analysis.signals import count_peaks, moving_average, robust_normalize, saturating


def test_robust_normalize_ignores_outliers():
    x = np.concatenate([np.linspace(0, 1, 100), [50.0]])
    n = robust_normalize(x)
    assert n.max() == 1.0
    assert n.min() == 0.0
    # The bulk of the data still spans most of the range despite the outlier.
    assert n[90] > 0.8


def test_robust_normalize_flat_signal_is_zero():
    assert np.all(robust_normalize(np.full(20, 0.3)) == 0.0)


def test_moving_average_keeps_length_and_smooths():
    x = np.zeros(50)
    x[25] = 1.0
    y = moving_average(x, 5)
    assert y.shape == x.shape
    assert y[25] == 0.2
    assert y.sum() == 1.0


def test_count_peaks_counts_bursts():
    t = np.linspace(0, 10, 200)
    x = 0.5 + 0.5 * np.sin(2 * np.pi * 0.8 * t)  # 8 peaks in 10 s
    assert count_peaks(x, min_prominence=0.3, min_distance=5) == 8


def test_count_peaks_ignores_jitter():
    rng = np.random.default_rng(0)
    x = 0.5 + rng.normal(0, 0.01, 200)
    assert count_peaks(x, min_prominence=0.1, min_distance=3) == 0


def test_saturating_curve():
    assert saturating(0, 10) == 0.0
    assert saturating(10, 10) == 0.5
    assert saturating(1000, 10) > 0.98

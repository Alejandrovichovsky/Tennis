"""Small numeric helpers shared by the analysis stages.

Pure numpy, no OpenCV - these are also the functions that are easiest to port
to Swift/Accelerate later.
"""

from __future__ import annotations

import numpy as np


def robust_normalize(x: np.ndarray, low_pct: float = 20.0, high_pct: float = 92.0) -> np.ndarray:
    """Map a signal to roughly 0..1 using percentiles instead of min/max.

    Min/max normalisation is hostage to a single outlier frame (a bird, a
    camera bump). Percentiles make the scale self-calibrating per venue:
    the same code works on a sunny clay court and a dim indoor hall.
    """
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x
    lo = float(np.percentile(x, low_pct))
    hi = float(np.percentile(x, high_pct))
    if hi - lo < 1e-9:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def moving_average(x: np.ndarray, window: int) -> np.ndarray:
    """Centred moving average with edge padding (keeps length)."""
    x = np.asarray(x, dtype=np.float64)
    if window <= 1 or x.size == 0:
        return x.copy()
    window = min(window, x.size)
    if window % 2 == 0:
        window += 1
    pad = window // 2
    padded = np.pad(x, pad, mode="edge")
    kernel = np.ones(window, dtype=np.float64) / window
    return np.convolve(padded, kernel, mode="valid")


def count_peaks(x: np.ndarray, *, min_prominence: float, min_distance: int) -> int:
    """Count local maxima that stick out of their surroundings.

    Used to estimate shot count from the activity signal when we have no
    reliable ball track: each stroke shows up as a burst of player motion.
    Deliberately simple - a scipy-free local-maximum scan with a prominence
    test against the lower of the two neighbouring valleys.
    """
    x = np.asarray(x, dtype=np.float64)
    n = x.size
    if n < 3:
        return 0

    peaks: list[int] = []
    for i in range(1, n - 1):
        if not (x[i] >= x[i - 1] and x[i] > x[i + 1]):
            continue
        # Walk downhill in both directions to the nearest valley floor.
        j = i - 1
        while j > 0 and x[j - 1] <= x[j]:
            j -= 1
        k = i + 1
        while k < n - 1 and x[k + 1] <= x[k]:
            k += 1
        prominence = x[i] - max(x[j], x[k])
        if prominence >= min_prominence:
            peaks.append(i)

    if not peaks:
        return 0

    # Enforce a minimum spacing, keeping the strongest peak in each cluster.
    kept: list[int] = []
    for p in sorted(peaks, key=lambda idx: x[idx], reverse=True):
        if all(abs(p - q) >= min_distance for q in kept):
            kept.append(p)
    return len(kept)


def saturating(value: float, saturation: float) -> float:
    """0..1 curve that rises fast and then flattens.

    A 15-shot rally is much better than a 3-shot rally; a 30-shot rally is not
    twice as good as a 15-shot one. ``value / (value + saturation)`` rescaled
    so that ``value == saturation`` maps to 0.5.
    """
    if saturation <= 0:
        return 0.0
    v = max(0.0, float(value))
    return v / (v + saturation)

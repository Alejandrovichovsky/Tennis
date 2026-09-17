"""Ball-hit detection from the audio track.

The cheapest strong signal in a tennis video is the sound of the ball: a
racket impact is a short broadband transient that nothing else on court
produces at that rate. Wind, talk and traffic live below 2 kHz; a hit puts
energy well above it for ~10 ms. So we measure energy in a 2-6 kHz band at
100 Hz, take how far it pops above a slow local median (onset strength),
and pick peaks above an adaptive threshold.

Why this matters more than it sounds: the far player is a handful of pixels
wide from behind the baseline, so "are both players moving" is unreliable
on real footage - while his hits are still audible. Audio is independent
of perspective, lighting and player size.

Failure modes, accepted for the MVP: shoe squeaks on hard court and hand
claps also register as hits. Both mostly happen during or right after
points, so they hurt shot *counts* more than rally *detection*.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import numpy as np

from ..config import Config
from ..video.ffmpeg import ffmpeg_exe, has_audio_stream

SAMPLE_RATE = 16000
HOP = 160          # 10 ms at 16 kHz
WIN = 512


@dataclass
class AudioHits:
    times: np.ndarray        # seconds of detected hits
    onset_t: np.ndarray      # 100 Hz onset-strength timeline (for plots)
    onset: np.ndarray
    threshold: float

    def to_dict(self) -> dict:
        return {
            "n_hits": int(self.times.size),
            "threshold": round(float(self.threshold), 4),
            "times": [round(float(t), 2) for t in self.times],
        }


def load_mono(path: str, *, max_seconds: float | None = None) -> np.ndarray:
    """Decode the audio track to float32 mono at 16 kHz via ffmpeg."""
    args = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", path]
    if max_seconds:
        args += ["-t", f"{max_seconds:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-500:])
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def onset_strength(x: np.ndarray, *, sr: int = SAMPLE_RATE, lo_hz: float = 2000.0, hi_hz: float = 6000.0) -> tuple[np.ndarray, np.ndarray]:
    """High-band log energy minus its slow local median, at 100 Hz."""
    if x.size < WIN * 2:
        return np.zeros(0), np.zeros(0)
    n = (x.size - WIN) // HOP
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n, WIN), strides=(x.strides[0] * HOP, x.strides[0])
    )
    window = np.hanning(WIN).astype(np.float32)
    spec = np.abs(np.fft.rfft(frames * window, axis=1))
    freqs = np.fft.rfftfreq(WIN, 1.0 / sr)
    band = (freqs >= lo_hz) & (freqs <= hi_hz)
    energy = np.log1p(1e3 * np.sum(spec[:, band] ** 2, axis=1))

    # Slow background: median over the previous ~0.5 s. A running median is
    # what makes the detector indifferent to a steady wind or a lawnmower.
    k = 50
    padded = np.concatenate([np.full(k, energy[0]), energy])
    windows = np.lib.stride_tricks.sliding_window_view(padded, k + 1)
    background = np.median(windows, axis=1)
    onset = np.clip(energy - background, 0.0, None)
    t = np.arange(n) * HOP / sr
    return t, onset


def pick_hits(t: np.ndarray, onset: np.ndarray, *, min_spacing_s: float = 0.22,
              sensitivity: float = 7.0) -> tuple[np.ndarray, float]:
    """Peaks that stand ``sensitivity`` robust-sigmas above the noise floor.

    The floor is estimated from the *positive* onset values only: the onset
    is clipped at zero and about half the frames sit exactly there, so plain
    median/MAD would collapse to 0 and pass everything. On a real match the
    positive part has median ~0.5 and sigma ~0.6; racket hits reach 5-7 and
    footsteps/neighbouring courts 3.5-4, so ~7 sigma separates them.
    """
    if onset.size == 0:
        return np.zeros(0), 0.0
    positive = onset[onset > 0]
    if positive.size < 10:
        return np.zeros(0), 0.0
    med = float(np.median(positive))
    sigma = 1.4826 * float(np.median(np.abs(positive - med))) + 1e-6
    threshold = max(med + sensitivity * sigma, 0.6)

    idx = np.where((onset[1:-1] > threshold) & (onset[1:-1] >= onset[:-2]) & (onset[1:-1] > onset[2:]))[0] + 1
    hits: list[float] = []
    for i in idx:
        if not hits or t[i] - hits[-1] >= min_spacing_s:
            hits.append(float(t[i]))
    return np.array(hits), threshold


def detect_hits(video_path: str, cfg: Config, *, max_seconds: float | None = None) -> AudioHits | None:
    """Full audio pass. Returns None when the file has no audio track."""
    if not has_audio_stream(video_path):
        return None
    x = load_mono(video_path, max_seconds=max_seconds)
    t, onset = onset_strength(x)
    times, thr = pick_hits(t, onset, min_spacing_s=cfg.audio.min_hit_spacing_s,
                           sensitivity=cfg.audio.sensitivity)
    return AudioHits(times=times, onset_t=t, onset=onset, threshold=thr)


def hit_density(hit_times: np.ndarray, sample_t: np.ndarray, *, window_s: float) -> np.ndarray:
    """Hits per second around each sample time, for the activity signal."""
    if hit_times.size == 0 or sample_t.size == 0:
        return np.zeros(sample_t.size)
    hits = np.sort(hit_times)
    lo = np.searchsorted(hits, sample_t - window_s, side="left")
    hi = np.searchsorted(hits, sample_t + window_s, side="right")
    return (hi - lo) / (2.0 * window_s)


def hits_between(hit_times: np.ndarray, start_s: float, end_s: float) -> int:
    if hit_times.size == 0:
        return 0
    hits = np.sort(hit_times)
    return int(np.searchsorted(hits, end_s, side="right") - np.searchsorted(hits, start_s, side="left"))

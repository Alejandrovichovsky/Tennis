"""End-to-end on a synthetic match. Slow (encodes video), so marked.

Run with:  pytest -m slow
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tennishl.config import Config
from tennishl.pipeline import analyze
from tennishl.synth import SynthSpec, generate


def _matches(truth, segments, tol=1.5):
    hits = 0
    for tp in truth:
        if any(abs(s["start_s"] - tp.start_s) <= tol and abs(s["end_s"] - tp.end_s) <= tol for s in segments):
            hits += 1
    return hits


@pytest.mark.slow
def test_synthetic_match_points_are_found(tmp_path: Path):
    video = tmp_path / "synth.mp4"
    points = generate(video, SynthSpec(seconds=60.0, seed=11, width=960, height=540))
    assert len(points) >= 3

    out = tmp_path / "out"
    cfg = Config()
    result = analyze(video, out, cfg, skip_clips=True, skip_ball=True)

    analysis = json.loads((out / "analysis.json").read_text())
    segments = analysis["segmentation"]["segments"]
    found = _matches(points, segments)
    assert found >= len(points) - 1, f"found {found}/{len(points)}: {segments}"
    # Dead time must be cut: total active time well below the video length.
    assert sum(s["duration_s"] for s in segments) < 0.6 * 60.0

    assert (out / "highlights.json").exists()
    assert (out / "report.md").exists()
    assert (out / "review.html").exists()
    assert result.court.confidence > 0.5


@pytest.mark.slow
def test_camera_bump_is_rejected(tmp_path: Path):
    video = tmp_path / "bump.mp4"
    points = generate(video, SynthSpec(seconds=45.0, seed=5, width=960, height=540, camera_bump_at_s=20.0))
    out = tmp_path / "out"
    analyze(video, out, Config(), skip_clips=True, skip_ball=True)
    analysis = json.loads((out / "analysis.json").read_text())
    # Whatever happens around the bump must not become a highlight.
    for s in analysis["segmentation"]["segments"]:
        assert not (s["start_s"] <= 20.0 <= s["end_s"]), s
    assert len(points) >= 2


@pytest.mark.slow
def test_full_render_produces_montage(tmp_path: Path):
    video = tmp_path / "short.mp4"
    generate(video, SynthSpec(seconds=40.0, seed=2, width=640, height=360))
    out = tmp_path / "out"
    cfg = Config().merged({"clip": {"max_highlights": 2}})
    result = analyze(video, out, cfg)
    assert result.montage_path is not None and result.montage_path.exists()
    assert result.montage_path.stat().st_size > 10_000
    clips = list((out / "clips").glob("*.mp4"))
    assert 1 <= len(clips) <= 2

"""End-to-end orchestration.

    video -> proxy -> observations -> court -> activity -> segments
          -> features -> scoring -> (ball tracking) -> clips -> montage

Each arrow is a function call on plain data, so any stage can be swapped or
re-run in isolation. ``analyze()`` writes everything to an output folder;
``render()`` re-cuts from a saved analysis plus a user's selection.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .analysis import (
    Box,
    CourtModel,
    apply_clip_padding,
    build_highlights,
    build_player_tracks,
    compute_activity,
    estimate_court,
    extract_features,
    merge_adjacent_clips,
    run_coarse_pass,
    segment_signal,
    select_players,
    track_ball_in_window,
)
from .analysis.scoring import categorise, score_features
from .config import Config
from .progress import Progress
from .render import (
    build_montage,
    cut_highlight,
    write_report,
    write_review_html,
    write_selection,
    write_thumbnails,
)
from .types import BallPoint, BallTrack, Highlight, RallyFeatures, Segment, VideoInfo
from .video import probe

ANALYSIS_FILE = "analysis.json"
HIGHLIGHTS_FILE = "highlights.json"
SELECTION_FILE = "selection.json"
REPORT_FILE = "report.md"
REVIEW_FILE = "review.html"
MONTAGE_FILE = "highlights.mp4"
CLIPS_DIR = "clips"
THUMBS_DIR = "thumbs"


@dataclass
class AnalyzeResult:
    info: VideoInfo
    court: CourtModel
    highlights: list[Highlight]
    n_segments: int
    n_rejected: int
    montage_path: Path | None
    out_dir: Path


def _exclusion_boxes(observations, court: CourtModel, scale: float) -> list[tuple[float, list[Box]]]:
    """Player boxes from the coarse pass, scaled to the ball-proxy resolution."""
    out: list[tuple[float, list[Box]]] = []
    for obs in observations:
        near, far = select_players(obs.blobs, court)
        boxes: list[Box] = []
        for b in (near, far):
            if b is None:
                continue
            x, y, w, h = b.box
            boxes.append(Box(x * scale, y * scale, (x + w) * scale, (y + h) * scale))
        out.append((obs.t, boxes))
    return out


def analyze(
    video_path: str | Path,
    out_dir: str | Path,
    cfg: Config | None = None,
    *,
    progress: Progress | None = None,
    max_seconds: float | None = None,
    skip_clips: bool = False,
    skip_ball: bool = False,
) -> AnalyzeResult:
    cfg = cfg or Config()
    progress = progress or Progress(quiet=True)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    info = probe(video_path)
    if max_seconds and max_seconds > 0:
        info.duration_s = min(info.duration_s, max_seconds) if info.duration_s else max_seconds
    progress.log(
        f"Video: {info.width}x{info.height} @ {info.fps:.2f} fps, "
        f"{info.duration_s:.0f} s, {info.frame_count} frames"
    )

    # 1. Coarse pass ---------------------------------------------------
    progress.stage("Analyserar rörelse")
    coarse = run_coarse_pass(info, cfg, progress=progress.update, max_seconds=max_seconds)
    observations = coarse.observations
    progress.done(f"{len(observations)} samplade bilder @ {coarse.sample_fps:g} fps")
    if len(observations) < 10:
        raise RuntimeError("Videon är för kort eller kunde inte läsas.")

    # 2. Court + activity + segmentation --------------------------------
    progress.stage("Hittar bana och spelare")
    court = estimate_court(coarse.motion_map, observations, coarse.proxy_size, cfg)
    signal = compute_activity(observations, court, cfg)
    seg_result = segment_signal(signal.t, signal.smoothed, signal.spread, signal.camera_motion, cfg)
    segments = seg_result.segments
    progress.done(
        f"bana confidence {court.confidence:.2f}, nätlinje y={court.net_y:.0f}px; "
        f"{len(segments)} aktiva poäng, {len(seg_result.rejected)} förkastade"
    )

    # 3. Features + scoring --------------------------------------------
    progress.stage("Rankar poäng")
    tracks = build_player_tracks(observations, court)
    features = [extract_features(s, observations, signal, tracks, court, cfg) for s in segments]
    highlights = build_highlights(segments, features, cfg)
    progress.done(f"{sum(1 for h in highlights if h.selected)} klipp valda av {len(highlights)}")

    # 4. Ball tracking on selected clips only ---------------------------
    if not skip_ball and highlights:
        chosen = [h for h in highlights if h.selected]
        progress.stage("Spårar bollen")
        ball_scale = (cfg.proxy.ball_width / float(info.width)) / (coarse.proxy_size[0] / float(info.width))
        excl = _exclusion_boxes(observations, court, ball_scale)
        for i, h in enumerate(chosen):
            window = [(t, b) for t, b in excl if h.segment.start_s - 1 <= t <= h.segment.end_s + 1]
            try:
                track = track_ball_in_window(
                    info, h.segment.start_s, h.segment.end_s, cfg, exclusion_boxes_by_time=window
                )
            except Exception as exc:  # ball module must never take the pipeline down
                progress.log(f"  bollspårning misslyckades för {h.id}: {exc}")
                track = None
            h.ball_track = track
            if track is not None and track.confidence >= cfg.ball.min_confidence:
                # Re-derive shot count from the ball and re-score.
                h.features = extract_features(
                    h.segment, observations, signal, tracks, court, cfg, ball_track=track
                )
                h.score, h.contributions = score_features(h.features, cfg)
                h.categories = categorise(h.features, cfg)
            progress.update(i + 1, len(chosen))
        n_trails = sum(
            1 for h in chosen if h.ball_track and h.ball_track.confidence >= cfg.ball.min_confidence
        )
        progress.done(f"bollspår med tillräcklig confidence: {n_trails}/{len(chosen)}")

        # Scores may have moved; keep the ranking honest.
        highlights.sort(key=lambda h: h.score, reverse=True)
        for rank, h in enumerate(highlights, start=1):
            h.rank = rank

    # 5. Padding + merge ------------------------------------------------
    apply_clip_padding(highlights, cfg, info.duration_s)
    merge_adjacent_clips(highlights, cfg)

    # 6. Persist analysis before the slow render step -------------------
    _write_analysis(out_dir, info, cfg, court, signal, seg_result, coarse.sample_fps)
    _write_highlights(out_dir, info, highlights)
    write_selection(out_dir / SELECTION_FILE, highlights)

    # 7. Cut + montage --------------------------------------------------
    montage_path: Path | None = None
    if not skip_clips:
        montage_path = _render_clips(info, highlights, out_dir, cfg, progress)
        _write_highlights(out_dir, info, highlights)  # now with clip paths

    # 8. Human-facing outputs ------------------------------------------
    thumbs = write_thumbnails(info, highlights, out_dir / THUMBS_DIR)
    write_review_html(
        out_dir / REVIEW_FILE, info, highlights, thumbs,
        thumbs_dir_name=THUMBS_DIR, min_ball_confidence=cfg.ball.min_confidence,
    )
    write_report(
        out_dir / REPORT_FILE, info, highlights,
        n_segments=len(segments), n_rejected=len(seg_result.rejected),
        court_confidence=court.confidence, min_ball_confidence=cfg.ball.min_confidence,
        montage_path=montage_path,
    )
    progress.log(f"Klart på {time.time() - t0:.0f} s -> {out_dir}")

    return AnalyzeResult(
        info=info, court=court, highlights=highlights,
        n_segments=len(segments), n_rejected=len(seg_result.rejected),
        montage_path=montage_path, out_dir=out_dir,
    )


def _render_clips(info: VideoInfo, highlights: list[Highlight], out_dir: Path, cfg: Config, progress: Progress) -> Path | None:
    chosen = sorted([h for h in highlights if h.selected], key=lambda h: h.clip_start_s)
    if not chosen:
        progress.log("Inga klipp valda - inget montage.")
        return None
    progress.stage("Klipper")
    clip_dir = out_dir / CLIPS_DIR
    paths: list[Path] = []
    for i, h in enumerate(chosen):
        paths.append(cut_highlight(info, h, clip_dir, cfg, ball_proxy_width=cfg.proxy.ball_width))
        progress.update(i + 1, len(chosen))
    progress.done(f"{len(paths)} klipp i {clip_dir}")

    progress.stage("Bygger montage")
    montage = build_montage(paths, out_dir / MONTAGE_FILE)
    progress.done(f"{montage}")
    return montage


def render(out_dir: str | Path, cfg: Config | None = None, *, progress: Progress | None = None,
           selection_path: str | Path | None = None, recut: bool = False) -> Path | None:
    """Re-render the montage from a saved analysis and an (edited) selection."""
    cfg = cfg or Config()
    progress = progress or Progress(quiet=True)
    out_dir = Path(out_dir)

    data = json.loads((out_dir / HIGHLIGHTS_FILE).read_text(encoding="utf-8"))
    info = VideoInfo(**data["video"])
    highlights = [_highlight_from_dict(d) for d in data["highlights"]]

    sel_path = Path(selection_path) if selection_path else out_dir / SELECTION_FILE
    if sel_path.exists():
        sel = json.loads(sel_path.read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in sel.get("clips", [])}
        for h in highlights:
            if h.id in by_id:
                c = by_id[h.id]
                h.selected = bool(c.get("selected", h.selected))
                h.clip_start_s = float(c.get("start_s", h.clip_start_s))
                h.clip_end_s = float(c.get("end_s", h.clip_end_s))
        progress.log(f"Urval laddat fran {sel_path.name}")

    chosen = sorted([h for h in highlights if h.selected], key=lambda h: h.clip_start_s)
    if not chosen:
        progress.log("Inga klipp valda.")
        return None

    clip_dir = out_dir / CLIPS_DIR
    progress.stage("Klipper")
    paths: list[Path] = []
    for i, h in enumerate(chosen):
        existing = Path(h.clip_path) if h.clip_path else None
        if not recut and existing and existing.exists() and _clip_matches(existing, h):
            paths.append(existing)
        else:
            paths.append(cut_highlight(info, h, clip_dir, cfg, ball_proxy_width=cfg.proxy.ball_width))
        progress.update(i + 1, len(chosen))
    progress.done()

    progress.stage("Bygger montage")
    montage = build_montage(paths, out_dir / MONTAGE_FILE)
    progress.done(f"{montage}")
    _write_highlights(out_dir, info, highlights)
    return montage


def _clip_matches(path: Path, h: Highlight) -> bool:
    # Clip files encode start second in their name; if the user moved the
    # boundaries in selection.json we must re-cut.
    return path.name.endswith(f"_{int(h.clip_start_s):05d}s.mp4")


# ----------------------------------------------------------------------
# Serialisation
# ----------------------------------------------------------------------

def _write_analysis(out_dir: Path, info: VideoInfo, cfg: Config, court: CourtModel, signal, seg_result, sample_fps: float) -> None:
    # Store the activity signal at 2 Hz to keep the file readable; the full
    # signal is reconstructible by re-running the coarse pass.
    step = max(1, int(round(sample_fps / 2.0)))
    payload = {
        "video": info.to_dict(),
        "config": cfg.to_dict(),
        "court": court.to_dict(),
        "segmentation": seg_result.to_dict(),
        "signal": {
            "sample_dt": round(signal.sample_dt * step, 4),
            "t": [round(float(v), 2) for v in signal.t[::step]],
            "activity": [round(float(v), 3) for v in signal.smoothed[::step]],
            "speed": [round(float(v), 3) for v in signal.speed[::step]],
            "fg": [round(float(v), 3) for v in signal.fg[::step]],
            "spread": [int(v) for v in signal.spread[::step]],
            "camera_motion": [round(float(v), 3) for v in signal.camera_motion[::step]],
        },
    }
    (out_dir / ANALYSIS_FILE).write_text(json.dumps(payload, indent=1), encoding="utf-8")


def _write_highlights(out_dir: Path, info: VideoInfo, highlights: list[Highlight]) -> None:
    payload = {"video": info.to_dict(), "highlights": [h.to_dict() for h in highlights]}
    (out_dir / HIGHLIGHTS_FILE).write_text(json.dumps(payload, indent=1), encoding="utf-8")


def _highlight_from_dict(d: dict) -> Highlight:
    seg = d["segment"]
    segment = Segment(
        start_s=seg["start_s"], end_s=seg["end_s"], mean_activity=seg["mean_activity"],
        peak_activity=seg["peak_activity"], both_sides_frac=seg["both_sides_frac"],
        camera_motion=seg["camera_motion"],
    )
    features = RallyFeatures(**d["features"])
    track = None
    if d.get("ball_track"):
        bt = d["ball_track"]
        track = BallTrack(
            points=[BallPoint(t=p["t"], x=p["x"], y=p["y"], radius_px=p["r"], interpolated=p["i"]) for p in bt["points"]],
            confidence=bt["confidence"], rms_residual_px=bt["rms_residual_px"],
        )
    return Highlight(
        id=d["id"], segment=segment, features=features, score=d["score"], categories=d["categories"],
        contributions=d.get("contributions", {}), rank=d["rank"], selected=d["selected"],
        clip_start_s=d["clip_start_s"], clip_end_s=d["clip_end_s"], ball_track=track,
        clip_path=d.get("clip_path"),
    )

"""Command line interface.

    tennishl analyze match.mp4 -o out/        # full pipeline
    tennishl render out/                      # re-cut after editing selection.json
    tennishl debug match.mp4 -o out/debug/    # signal plot + annotated proxy video
    tennishl synth demo.mp4 --seconds 90      # synthetic test footage
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .progress import Progress


def _config_from_args(args: argparse.Namespace) -> Config:
    cfg = Config.load(getattr(args, "config", None))
    overrides: dict = {}
    clip: dict = {}
    if getattr(args, "top", None) is not None:
        clip["max_highlights"] = int(args.top)
    if getattr(args, "pre", None) is not None:
        clip["pre_roll_s"] = float(args.pre)
    if getattr(args, "post", None) is not None:
        clip["post_roll_s"] = float(args.post)
    if getattr(args, "no_trail", False):
        clip["draw_ball_trail"] = False
    if clip:
        overrides["clip"] = clip
    if getattr(args, "coarse_fps", None) is not None:
        overrides["proxy"] = {"coarse_fps": float(args.coarse_fps)}
    return cfg.merged(overrides) if overrides else cfg


def cmd_analyze(args: argparse.Namespace) -> int:
    from .pipeline import analyze

    cfg = _config_from_args(args)
    video = Path(args.video)
    out_dir = Path(args.out) if args.out else video.parent / f"{video.stem}_highlights"
    progress = Progress(json_lines=args.json_progress, quiet=args.quiet)
    result = analyze(
        args.video, out_dir, cfg, progress=progress, max_seconds=args.max_seconds,
        skip_clips=args.no_clips, skip_ball=args.no_ball,
    )
    if not args.quiet:
        from .render.review import describe_highlight, fmt_time

        print()
        print(f"Poäng hittade: {result.n_segments}   förkastade: {result.n_rejected}   bana: {result.court.confidence:.2f}")
        for h in sorted([h for h in result.highlights if h.selected], key=lambda h: h.clip_start_s):
            print(f"  [{h.rank:2d}] {fmt_time(h.clip_start_s)}-{fmt_time(h.clip_end_s)}  {h.score:.2f}  {describe_highlight(h, cfg.ball.min_confidence)}")
        print()
        print(f"Rapport:  {out_dir / 'report.md'}")
        print(f"Granska:  {out_dir / 'review.html'}")
        if result.montage_path:
            print(f"Montage:  {result.montage_path}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    from .pipeline import render

    cfg = _config_from_args(args)
    progress = Progress(json_lines=args.json_progress, quiet=args.quiet)
    montage = render(args.out_dir, cfg, progress=progress, selection_path=args.selection, recut=args.recut)
    if montage and not args.quiet:
        print(f"Montage:  {montage}")
    return 0


def cmd_debug(args: argparse.Namespace) -> int:
    from .analysis import compute_activity, estimate_court, run_coarse_pass, segment_signal
    from .render.debug import write_debug_video, write_signal_plot
    from .video import probe

    cfg = _config_from_args(args)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    progress = Progress(quiet=args.quiet)
    info = probe(args.video)
    progress.stage("Analyserar rörelse")
    coarse = run_coarse_pass(info, cfg, progress=progress.update, max_seconds=args.max_seconds)
    progress.done()
    court = estimate_court(coarse.motion_map, coarse.observations, coarse.proxy_size, cfg)
    signal = compute_activity(coarse.observations, court, cfg)
    seg = segment_signal(signal.t, signal.smoothed, signal.spread, signal.camera_motion, cfg)
    write_signal_plot(out_dir / "signal.png", signal, seg)
    progress.log(f"signal.png skriven ({len(seg.segments)} segment, {len(seg.rejected)} förkastade)")
    if not args.no_video:
        progress.stage("Ritar debugvideo")
        write_debug_video(out_dir / "debug.mp4", info, coarse.observations, court, seg, cfg, max_seconds=args.max_seconds)
        progress.done(f"{out_dir / 'debug.mp4'}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .web import serve

    print(f"tennishl  http://{args.host}:{args.port}   (data: {Path(args.data_dir).resolve()})")
    serve(args.data_dir, host=args.host, port=args.port)
    return 0


def cmd_retune(args: argparse.Namespace) -> int:
    from .pipeline import retune

    cfg = _config_from_args(args)
    progress = Progress(json_lines=args.json_progress, quiet=args.quiet)
    result = retune(args.out_dir, cfg, progress=progress, skip_clips=args.no_clips, skip_ball=args.no_ball)
    if not args.quiet:
        print(f"Poäng hittade: {result.n_segments}   förkastade: {result.n_rejected}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    import json as _json

    from .eval import evaluate_files
    from .render.review import fmt_time

    r = evaluate_files(Path(args.out_dir) / "analysis.json", args.truth)
    print(f"recall {r.recall:.2f} ({r.matched}/{r.n_truth})   precision {r.precision:.2f} ({r.matched}/{r.n_detected})")
    print(f"startfel {r.mean_start_error_s:.2f} s   slutfel {r.mean_end_error_s:.2f} s")
    if r.missed:
        print("missade:  " + ", ".join(fmt_time(m["start_s"]) for m in r.missed))
    if r.false_positives:
        print("falska:   " + ", ".join(fmt_time(m["start_s"]) for m in r.false_positives))
    if args.json:
        print(_json.dumps(r.to_dict(), indent=1))
    return 0


def cmd_synth(args: argparse.Namespace) -> int:
    from .synth import SynthSpec, generate, write_ground_truth

    spec = SynthSpec(seconds=args.seconds, seed=args.seed, camera_bump_at_s=args.bump_at)
    points = generate(args.out, spec)
    write_ground_truth(points, Path(args.out).with_suffix(".truth.json"))
    print(f"{args.out}: {len(points)} poäng, facit i {Path(args.out).with_suffix('.truth.json')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tennishl", description="Automatiska tennis-highlights från en statisk kamera.")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--config", help="JSON med konfig-overrides")
        sp.add_argument("--top", type=int, help="max antal klipp")
        sp.add_argument("--pre", type=float, help="sekunder före varje poäng")
        sp.add_argument("--post", type=float, help="sekunder efter varje poäng")
        sp.add_argument("--no-trail", action="store_true", help="rita aldrig bollspår")
        sp.add_argument("--coarse-fps", type=float, help="samplingsfrekvens för grovanalysen")
        sp.add_argument("--json-progress", action="store_true", help="progress som JSON-rader på stderr")
        sp.add_argument("--quiet", action="store_true")

    a = sub.add_parser("analyze", help="analysera en video och skapa highlights")
    a.add_argument("video")
    a.add_argument("-o", "--out", help="utmapp (default: <video>_highlights)")
    a.add_argument("--max-seconds", type=float, help="analysera bara de första N sekunderna")
    a.add_argument("--no-clips", action="store_true", help="bara analys, klipp inget")
    a.add_argument("--no-ball", action="store_true", help="hoppa över bollspårning")
    common(a)
    a.set_defaults(func=cmd_analyze)

    r = sub.add_parser("render", help="klipp om från sparad analys + selection.json")
    r.add_argument("out_dir")
    r.add_argument("--selection", help="annan selection.json")
    r.add_argument("--recut", action="store_true", help="klipp om alla klipp även om de finns")
    common(r)
    r.set_defaults(func=cmd_render)

    d = sub.add_parser("debug", help="signalplot + annoterad proxyvideo")
    d.add_argument("video")
    d.add_argument("-o", "--out", required=True)
    d.add_argument("--max-seconds", type=float)
    d.add_argument("--no-video", action="store_true")
    common(d)
    d.set_defaults(func=cmd_debug)

    w = sub.add_parser("serve", help="lokal webapp")
    w.add_argument("--data-dir", default="tennishl_data", help="var jobb och utdata sparas")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8000)
    w.set_defaults(func=cmd_serve)

    t = sub.add_parser("retune", help="kör om allt utom avkodningen med ny konfig")
    t.add_argument("out_dir")
    t.add_argument("--no-clips", action="store_true")
    t.add_argument("--no-ball", action="store_true")
    common(t)
    t.set_defaults(func=cmd_retune)

    e = sub.add_parser("eval", help="jämför analys med facit")
    e.add_argument("out_dir")
    e.add_argument("truth", help="truth.json: [{start_s, end_s}]")
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=cmd_eval)

    s = sub.add_parser("synth", help="generera syntetisk testvideo")
    s.add_argument("out")
    s.add_argument("--seconds", type=float, default=90.0)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--bump-at", type=float, default=None, help="simulera kamerastöt vid sekund N")
    s.set_defaults(func=cmd_synth)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\navbrutet", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

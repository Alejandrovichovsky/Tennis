"""Human-facing output: a Markdown report, a review page and thumbnails.

The review page is a single self-contained HTML file (no server, works
offline). The user ticks/unticks clips and downloads ``selection.json``,
which ``tennishl render`` then honours. This is the desktop stand-in for the
"toggle highlights" list in the app.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import cv2

from ..analysis.scoring import CATEGORY_LABELS_SV
from ..types import Highlight, VideoInfo


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def describe_highlight(h: Highlight, min_ball_confidence: float) -> str:
    """One line of plain-Swedish feedback about why this clip was picked."""
    f = h.features
    cats = ", ".join(CATEGORY_LABELS_SV.get(c, c) for c in h.categories)
    top = sorted(h.contributions.items(), key=lambda kv: kv[1], reverse=True)[:2]
    reasons = {
        "duration": "längden",
        "shots": "antalet slag",
        "intensity": "tempot",
        "coverage": "att spelarna rörde sig över hela banan",
        "finish": "ett tydligt avslut",
        "serve": "en tydlig servestart",
    }
    why = " och ".join(reasons.get(k, k) for k, v in top if v > 0) or "helheten"
    shots = f"~{f.shot_count:.0f} slag" + (" (bollspår)" if f.shot_source == "ball" else " (rörelse)")
    trail = (
        f"bollspår {h.ball_track.confidence:.2f}"
        if h.ball_track and h.ball_track.confidence >= min_ball_confidence
        else "inget bollspår"
    )
    return f"{cats}. {f.duration_s:.1f} s, {shots}. Valdes framför allt på grund av {why}. {trail}."


def write_thumbnails(info: VideoInfo, highlights: list[Highlight], out_dir: Path, width: int = 480) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(info.path)
    paths: dict[str, str] = {}
    try:
        for h in highlights:
            mid = (h.segment.start_s + h.segment.end_s) / 2.0
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(mid * info.fps)))
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            scale = width / float(frame.shape[1])
            small = cv2.resize(frame, (width, int(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
            p = out_dir / f"{h.id}.jpg"
            cv2.imwrite(str(p), small, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            paths[h.id] = p.name
    finally:
        cap.release()
    return paths


def write_report(
    out_path: Path,
    info: VideoInfo,
    highlights: list[Highlight],
    *,
    n_segments: int,
    n_rejected: int,
    court_confidence: float,
    min_ball_confidence: float,
    montage_path: Path | None,
) -> None:
    lines: list[str] = []
    lines.append(f"# Highlights: {Path(info.path).name}")
    lines.append("")
    lines.append(
        f"- Källa: {info.width}x{info.height} @ {info.fps:.2f} fps, {fmt_time(info.duration_s)} lång"
    )
    lines.append(f"- Aktiva poäng hittade: {n_segments} (förkastade kandidater: {n_rejected})")
    lines.append(f"- Banmodell (ROI + nätlinje), confidence: {court_confidence:.2f}")
    selected = [h for h in highlights if h.selected]
    total_len = sum(h.clip_duration_s for h in selected)
    lines.append(f"- Valda klipp: {len(selected)} st, totalt {fmt_time(total_len)}")
    if montage_path:
        lines.append(f"- Montage: `{montage_path.name}`")
    lines.append("")
    lines.append("## Valda klipp (i tidsordning)")
    lines.append("")
    lines.append("| # | Tid | Längd | Score | Kategori | Feedback |")
    lines.append("|---|-----|-------|-------|----------|----------|")
    for h in sorted(selected, key=lambda x: x.clip_start_s):
        cats = ", ".join(CATEGORY_LABELS_SV.get(c, c) for c in h.categories)
        lines.append(
            f"| {h.rank} | {fmt_time(h.clip_start_s)}-{fmt_time(h.clip_end_s)} | "
            f"{h.clip_duration_s:.1f} s | {h.score:.2f} | {cats} | "
            f"{describe_highlight(h, min_ball_confidence)} |"
        )
    lines.append("")
    lines.append("## Alla poäng, rankade")
    lines.append("")
    lines.append("| Rank | Vald | Tid | Längd | Score | Slag | Tempo | Avslut | Serve | Boll |")
    lines.append("|------|------|-----|-------|-------|------|-------|--------|-------|------|")
    for h in highlights:
        f = h.features
        ball = f"{h.ball_track.confidence:.2f}" if h.ball_track else "-"
        lines.append(
            f"| {h.rank} | {'x' if h.selected else ''} | {fmt_time(h.segment.start_s)} | "
            f"{f.duration_s:.1f} s | {h.score:.2f} | {f.shot_count:.0f} | {f.mean_intensity:.2f} | "
            f"{f.finish:.2f} | {f.serve_onset:.2f} | {ball} |"
        )
    lines.append("")
    lines.append("Ändra urvalet i `review.html` (eller `selection.json`) och kör `tennishl render`.")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_selection(out_path: Path, highlights: list[Highlight]) -> None:
    data = {
        "version": 1,
        "clips": [
            {
                "id": h.id,
                "rank": h.rank,
                "start_s": round(h.clip_start_s, 3),
                "end_s": round(h.clip_end_s, 3),
                "selected": bool(h.selected),
                "ball_trail": bool(h.ball_track is not None),
            }
            for h in highlights
        ],
    }
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_review_html(
    out_path: Path,
    info: VideoInfo,
    highlights: list[Highlight],
    thumbs: dict[str, str],
    *,
    thumbs_dir_name: str,
    min_ball_confidence: float,
) -> None:
    rows: list[str] = []
    for h in highlights:
        f = h.features
        cats = " ".join(
            f'<span class="tag">{html.escape(CATEGORY_LABELS_SV.get(c, c))}</span>' for c in h.categories
        )
        thumb = thumbs.get(h.id)
        img = f'<img src="{thumbs_dir_name}/{thumb}" alt="">' if thumb else '<div class="noimg"></div>'
        clip_link = (
            f'<a href="{html.escape(Path(h.clip_path).name)}" target="_blank">spela klipp</a>'
            if h.clip_path
            else ""
        )
        trail = (
            f"bollspår {h.ball_track.confidence:.2f}"
            if h.ball_track and h.ball_track.confidence >= min_ball_confidence
            else "inget bollspår"
        )
        rows.append(
            f"""
<label class="row" data-id="{h.id}">
  <input type="checkbox" {'checked' if h.selected else ''}>
  {img}
  <div class="meta">
    <div class="title">#{h.rank} &middot; {fmt_time(h.clip_start_s)}&ndash;{fmt_time(h.clip_end_s)}
      &middot; {h.clip_duration_s:.1f} s &middot; score {h.score:.2f} {clip_link}</div>
    <div class="tags">{cats}</div>
    <div class="why">{html.escape(describe_highlight(h, min_ball_confidence))}</div>
    <div class="nums">slag {f.shot_count:.0f} ({f.shot_source}) &middot; tempo {f.mean_intensity:.2f}
      &middot; täckning {f.coverage:.2f} &middot; avslut {f.finish:.2f} &middot; serve {f.serve_onset:.2f}
      &middot; nät {f.net_approach:.2f} &middot; {trail}</div>
  </div>
</label>"""
        )

    clips_json = json.dumps(
        [
            {"id": h.id, "rank": h.rank, "start_s": round(h.clip_start_s, 3),
             "end_s": round(h.clip_end_s, 3), "ball_trail": bool(h.ball_track is not None)}
            for h in highlights
        ]
    )

    page = f"""<!doctype html>
<html lang="sv"><head><meta charset="utf-8">
<title>Highlights - {html.escape(Path(info.path).name)}</title>
<style>
:root {{ --bg:#111; --fg:#eee; --muted:#999; --card:#1c1c1c; --accent:#d8ff3a; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.4 -apple-system, system-ui, sans-serif; padding:24px; }}
h1 {{ font-size:20px; margin:0 0 4px; }} .sub {{ color:var(--muted); margin-bottom:20px; }}
.row {{ display:grid; grid-template-columns:24px 240px 1fr; gap:14px; align-items:center;
  background:var(--card); padding:12px; border-radius:10px; margin-bottom:10px; cursor:pointer; }}
.row img, .noimg {{ width:240px; aspect-ratio:16/9; object-fit:cover; border-radius:6px; background:#000; }}
.row:has(input:not(:checked)) {{ opacity:.45; }}
.title {{ font-weight:600; }} .title a {{ color:var(--accent); margin-left:8px; font-weight:400; }}
.tag {{ display:inline-block; background:#2a2a2a; color:var(--accent); font-size:12px; padding:2px 8px;
  border-radius:99px; margin:4px 6px 4px 0; }}
.why {{ color:#ccc; }} .nums {{ color:var(--muted); font-size:13px; margin-top:4px; }}
.bar {{ position:sticky; top:0; background:var(--bg); padding:12px 0; display:flex; gap:12px; align-items:center; }}
button {{ background:var(--accent); color:#111; border:0; padding:10px 16px; border-radius:8px; font-weight:600; cursor:pointer; }}
.count {{ color:var(--muted); }}
</style></head><body>
<h1>Highlights - {html.escape(Path(info.path).name)}</h1>
<div class="sub">Bocka i/ur klipp, ladda ner <code>selection.json</code> till samma mapp och kör <code>tennishl render</code>.</div>
<div class="bar"><button id="dl">Ladda ner selection.json</button><span class="count" id="count"></span></div>
{''.join(rows)}
<script>
const clips = {clips_json};
function selected() {{
  const on = new Set([...document.querySelectorAll('.row')].filter(r => r.querySelector('input').checked).map(r => r.dataset.id));
  return clips.map(c => ({{...c, selected: on.has(c.id)}}));
}}
function update() {{
  const s = selected(); const n = s.filter(c => c.selected).length;
  const secs = s.filter(c => c.selected).reduce((a, c) => a + (c.end_s - c.start_s), 0);
  document.getElementById('count').textContent = n + ' klipp valda, ' + Math.round(secs) + ' s';
}}
document.querySelectorAll('.row input').forEach(i => i.addEventListener('change', update));
document.getElementById('dl').onclick = () => {{
  const blob = new Blob([JSON.stringify({{version: 1, clips: selected()}}, null, 2)], {{type: 'application/json'}});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'selection.json'; a.click();
}};
update();
</script></body></html>"""
    out_path.write_text(page, encoding="utf-8")

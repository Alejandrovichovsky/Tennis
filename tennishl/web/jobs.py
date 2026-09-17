"""Job manager for the local web UI.

One job = one video + one output folder. Jobs run in a single worker thread
(the pipeline is CPU-bound; two at once would just fight for cores) and
persist their state to ``job.json`` so a server restart keeps the list.

Layout under the data dir:

    jobs/<id>/job.json        status, paths, config overrides
    jobs/<id>/source.<ext>    uploaded video (absent when a local path is used)
    jobs/<id>/truth.json      hand-labelled points, if any
    jobs/<id>/out/            everything the pipeline writes
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..eval import evaluate
from ..pipeline import (
    ANALYSIS_FILE,
    HIGHLIGHTS_FILE,
    MONTAGE_FILE,
    OBSERVATIONS_FILE,
    analyze,
    render,
    retune,
)
from ..progress import Progress
from ..video.preview import make_preview, needs_preview

TRUTH_FILE = "truth.json"
PREVIEW_FILE = "preview.mp4"
JOB_FILE = "job.json"
MAX_LOG_LINES = 60


@dataclass
class JobState:
    id: str
    name: str
    video_path: str
    created_at: float
    status: str = "queued"        # queued | running | done | error
    action: str = "analyze"       # what is / was running
    stage: str = ""
    fraction: float = 0.0
    log: list[str] = field(default_factory=list)
    error: str | None = None
    config_overrides: dict[str, Any] = field(default_factory=dict)
    max_seconds: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobManager:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.jobs_dir = self.data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tennishl")
        self._load_existing()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_existing(self) -> None:
        for job_file in sorted(self.jobs_dir.glob("*/" + JOB_FILE)):
            try:
                data = json.loads(job_file.read_text(encoding="utf-8"))
                st = JobState(**data)
                if st.status in ("queued", "running"):
                    # The server died mid-run; nothing is running now.
                    st.status = "error"
                    st.error = "Avbruten (servern startades om)"
                self._jobs[st.id] = st
            except Exception:
                continue

    def _save(self, st: JobState) -> None:
        (self.job_dir(st.id) / JOB_FILE).write_text(json.dumps(st.to_dict(), indent=1), encoding="utf-8")

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def out_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "out"

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [self._summary(j) for j in jobs]

    def get(self, job_id: str) -> JobState | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _summary(self, st: JobState) -> dict[str, Any]:
        d = st.to_dict()
        out = self.out_dir(st.id)
        d["has_analysis"] = (out / ANALYSIS_FILE).exists()
        d["has_highlights"] = (out / HIGHLIGHTS_FILE).exists()
        d["has_observations"] = (out / OBSERVATIONS_FILE).exists()
        d["has_montage"] = (out / MONTAGE_FILE).exists()
        d["has_truth"] = (self.job_dir(st.id) / TRUTH_FILE).exists()
        d["source_url"] = self.source_url(st)
        return d

    def source_url(self, st: JobState) -> str | None:
        preview = self.job_dir(st.id) / PREVIEW_FILE
        if preview.exists():
            return f"/data/jobs/{st.id}/{PREVIEW_FILE}"
        p = Path(st.video_path)
        if not p.exists():
            return None
        try:
            rel = p.resolve().relative_to(self.data_dir.resolve())
            return f"/data/{rel.as_posix()}"
        except ValueError:
            return f"/api/jobs/{st.id}/source"

    def detail(self, job_id: str) -> dict[str, Any] | None:
        st = self.get(job_id)
        if st is None:
            return None
        d = self._summary(st)
        out = self.out_dir(job_id)
        base = f"/data/jobs/{job_id}/out"
        d["urls"] = {
            "out": base,
            "montage": f"{base}/{MONTAGE_FILE}" if d["has_montage"] else None,
            "signal_plot": f"{base}/signal.png" if (out / "signal.png").exists() else None,
            "report": f"{base}/report.md" if (out / "report.md").exists() else None,
        }
        highlights = _read_json(out / HIGHLIGHTS_FILE)
        if highlights:
            for h in highlights.get("highlights", []):
                cp = h.get("clip_path")
                h["clip_url"] = f"{base}/clips/{Path(cp).name}" if cp and Path(cp).exists() else None
                thumb = out / "thumbs" / f"{h['id']}.jpg"
                h["thumb_url"] = f"{base}/thumbs/{h['id']}.jpg" if thumb.exists() else None
        d["highlights"] = highlights
        analysis = _read_json(out / ANALYSIS_FILE)
        if analysis:
            # The full config is large and the UI only needs the tunables it shows.
            d["analysis"] = {k: v for k, v in analysis.items() if k != "config"}
            d["config"] = analysis.get("config")
        truth = _read_json(self.job_dir(job_id) / TRUTH_FILE)
        d["truth"] = truth
        if truth and analysis:
            d["eval"] = evaluate(truth.get("points", []), analysis["segmentation"]["segments"]).to_dict()
        return d

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    def create(self, *, name: str, video_path: str, config_overrides: dict | None = None,
               max_seconds: float | None = None) -> JobState:
        job_id = uuid.uuid4().hex[:10]
        st = JobState(
            id=job_id, name=name, video_path=str(video_path), created_at=time.time(),
            config_overrides=config_overrides or {}, max_seconds=max_seconds,
        )
        self.job_dir(job_id).mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._jobs[job_id] = st
        self._save(st)
        return st

    def upload_target(self, job_id: str, filename: str) -> Path:
        ext = Path(filename).suffix.lower() or ".mp4"
        return self.job_dir(job_id) / f"source{ext}"

    def delete(self, job_id: str) -> bool:
        with self._lock:
            st = self._jobs.pop(job_id, None)
        if st is None:
            return False
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)
        return True

    def save_truth(self, job_id: str, points: list[dict]) -> dict:
        points = sorted(
            ({"start_s": float(p["start_s"]), "end_s": float(p["end_s"])} for p in points if p["end_s"] > p["start_s"]),
            key=lambda p: p["start_s"],
        )
        payload = {"version": 1, "points": points}
        (self.job_dir(job_id) / TRUTH_FILE).write_text(json.dumps(payload, indent=1), encoding="utf-8")
        return payload

    def save_selection(self, job_id: str, selection: dict) -> None:
        out = self.out_dir(job_id)
        out.mkdir(parents=True, exist_ok=True)
        (out / "selection.json").write_text(json.dumps(selection, indent=1), encoding="utf-8")

    # ------------------------------------------------------------------
    # Running things
    # ------------------------------------------------------------------
    def start(self, job_id: str, action: str, *, config_overrides: dict | None = None,
              skip_ball: bool = False, skip_clips: bool = False) -> bool:
        st = self.get(job_id)
        if st is None or st.status == "running":
            return False
        with self._lock:
            st.status = "queued"
            st.action = action
            st.stage = ""
            st.fraction = 0.0
            st.error = None
            st.log = []
            if config_overrides is not None:
                st.config_overrides = config_overrides
        self._save(st)
        self._pool.submit(self._run, job_id, action, skip_ball, skip_clips)
        return True

    def _run(self, job_id: str, action: str, skip_ball: bool, skip_clips: bool) -> None:
        st = self.get(job_id)
        if st is None:
            return

        def on_event(ev: dict) -> None:
            with self._lock:
                if ev["type"] == "progress":
                    st.stage = ev["stage"]
                    st.fraction = ev["fraction"]
                elif ev["type"] == "log":
                    st.log.append(ev["message"])
                    del st.log[:-MAX_LOG_LINES]

        progress = Progress(quiet=True, on_event=on_event)
        with self._lock:
            st.status = "running"
        self._save(st)

        try:
            cfg = Config().merged(st.config_overrides or {})
            out = self.out_dir(job_id)
            out.mkdir(parents=True, exist_ok=True)
            if action == "analyze":
                analyze(st.video_path, out, cfg, progress=progress, max_seconds=st.max_seconds,
                        skip_ball=skip_ball, skip_clips=skip_clips)
                self._ensure_preview(st, progress)
            elif action == "retune":
                retune(out, cfg, progress=progress, skip_ball=skip_ball, skip_clips=skip_clips)
            elif action == "render":
                render(out, cfg, progress=progress)
            else:
                raise ValueError(f"okänd action: {action}")
            with self._lock:
                st.status = "done"
                st.fraction = 1.0
        except Exception as exc:
            with self._lock:
                st.status = "error"
                st.error = f"{exc}"
                st.log.append(traceback.format_exc().splitlines()[-1])
        finally:
            with self._lock:
                st.finished_at = time.time()
            self._save(st)


    def _ensure_preview(self, st: JobState, progress: Progress) -> None:
        """HEVC (iPhone default) does not play in Chromium: transcode once."""
        preview = self.job_dir(st.id) / PREVIEW_FILE
        if preview.exists() or not needs_preview(st.video_path):
            return
        progress.stage("Gör förhandsvisning (källan är inte H.264)")
        try:
            make_preview(st.video_path, preview)
            progress.done("preview.mp4 klar")
        except Exception as exc:
            progress.log(f"förhandsvisning misslyckades: {exc}")


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

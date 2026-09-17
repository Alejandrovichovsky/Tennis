"""FastAPI app: a thin HTTP layer over JobManager.

    tennishl serve            # http://127.0.0.1:8000

Everything is local: no auth, binds to loopback by default, files are served
straight from the data dir (the browser's <video> needs HTTP range requests,
which StaticFiles provides).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import Config
from .jobs import JobManager

STATIC_DIR = Path(__file__).parent / "static"


def create_app(data_dir: str | Path) -> FastAPI:
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    manager = JobManager(data_dir)

    app = FastAPI(title="tennishl", docs_url="/api/docs", redoc_url=None)
    app.state.manager = manager
    app.state.data_dir = data_dir

    # ------------------------------------------------------------------
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/config/default")
    def default_config() -> dict[str, Any]:
        return Config().to_dict()

    @app.get("/api/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return manager.list()

    @app.post("/api/jobs")
    async def create_job(
        file: UploadFile | None = File(default=None),
        path: str | None = Form(default=None),
        name: str | None = Form(default=None),
        max_seconds: float | None = Form(default=None),
        select_mode: str = Form(default="all"),
        max_highlights: int = Form(default=12),
        skip_ball: bool = Form(default=False),
        skip_clips: bool = Form(default=False),
    ) -> dict[str, Any]:
        if file is None and not path:
            raise HTTPException(400, "Skicka antingen en fil eller en lokal sökväg.")
        overrides = {"clip": {"select_mode": select_mode, "max_highlights": int(max_highlights)}}

        if path:
            p = Path(path).expanduser()
            if not p.exists():
                raise HTTPException(400, f"Hittar inte filen: {p}")
            st = manager.create(name=name or p.name, video_path=str(p.resolve()),
                                config_overrides=overrides, max_seconds=max_seconds)
        else:
            assert file is not None
            st = manager.create(name=name or (file.filename or "video"), video_path="",
                                config_overrides=overrides, max_seconds=max_seconds)
            target = manager.upload_target(st.id, file.filename or "video.mp4")
            with target.open("wb") as fh:
                shutil.copyfileobj(file.file, fh, length=4 * 1024 * 1024)
            st.video_path = str(target)
            manager._save(st)

        manager.start(st.id, "analyze", skip_ball=skip_ball, skip_clips=skip_clips)
        return manager.detail(st.id) or {}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        d = manager.detail(job_id)
        if d is None:
            raise HTTPException(404)
        return d

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str) -> dict[str, Any]:
        if not manager.delete(job_id):
            raise HTTPException(404)
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/source")
    def source(job_id: str) -> FileResponse:
        st = manager.get(job_id)
        if st is None or not Path(st.video_path).exists():
            raise HTTPException(404)
        return FileResponse(st.video_path, media_type="video/mp4")

    @app.post("/api/jobs/{job_id}/retune")
    async def retune_job(job_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        overrides = body.get("config", {})
        try:
            Config().merged(overrides)  # validate keys before we queue anything
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        ok = manager.start(job_id, "retune", config_overrides=overrides,
                           skip_ball=bool(body.get("skip_ball", False)),
                           skip_clips=bool(body.get("skip_clips", True)))
        if not ok:
            raise HTTPException(409, "Jobbet kör redan eller finns inte.")
        return manager.detail(job_id) or {}

    @app.post("/api/jobs/{job_id}/render")
    async def render_job(job_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        if "selection" in body:
            manager.save_selection(job_id, body["selection"])
        ok = manager.start(job_id, "render")
        if not ok:
            raise HTTPException(409, "Jobbet kör redan eller finns inte.")
        return manager.detail(job_id) or {}

    @app.put("/api/jobs/{job_id}/truth")
    async def put_truth(job_id: str, request: Request) -> dict[str, Any]:
        if manager.get(job_id) is None:
            raise HTTPException(404)
        body = await request.json()
        manager.save_truth(job_id, body.get("points", []))
        return manager.detail(job_id) or {}

    # Job outputs (clips, montage, thumbs, json) and uploaded sources.
    app.mount("/data", StaticFiles(directory=str(data_dir)), name="data")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.exception_handler(Exception)
    async def on_error(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=500)

    return app


def serve(data_dir: str | Path, *, host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(create_app(data_dir), host=host, port=port, log_level="warning")

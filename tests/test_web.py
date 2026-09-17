"""Web layer: API through TestClient (fast) and the real UI through
Playwright (slow, needs Chromium).

The Playwright test is the closest thing we have to "does the product work":
upload a synthetic match, wait for analysis, see cards, untick one, render,
label two points, save, read the eval numbers.
"""

from __future__ import annotations

import glob
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from tennishl.synth import SynthSpec, generate


@pytest.fixture(scope="module")
def synth_video(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("vid") / "synth.mp4"
    generate(p, SynthSpec(seconds=40.0, seed=2, width=640, height=360))
    return p


def _wait(fn, timeout=240.0, every=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise TimeoutError("timed out")


# ---------------------------------------------------------------- API ----

@pytest.mark.slow
def test_api_roundtrip(tmp_path: Path, synth_video: Path):
    from fastapi.testclient import TestClient

    from tennishl.web import create_app

    app = create_app(tmp_path / "data")
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        r = c.post("/api/jobs", data={"path": str(synth_video), "select_mode": "all", "skip_ball": "true"})
        assert r.status_code == 200, r.text
        job_id = r.json()["id"]

        d = _wait(lambda: (lambda j: j if j["status"] in ("done", "error") else None)(c.get(f"/api/jobs/{job_id}").json()))
        assert d["status"] == "done", d
        assert d["has_montage"] and d["urls"]["montage"]
        hl = d["highlights"]["highlights"]
        assert len(hl) >= 2
        assert all(h["clip_url"] for h in hl if h["selected"])
        assert c.get(d["urls"]["montage"]).status_code == 200

        # Range request on the montage - the <video> element depends on it.
        r = c.get(d["urls"]["montage"], headers={"Range": "bytes=0-99"})
        assert r.status_code == 206 and len(r.content) == 100

        # Truth + eval
        seg = d["analysis"]["segmentation"]["segments"][0]
        r = c.put(f"/api/jobs/{job_id}/truth", json={"points": [{"start_s": seg["start_s"], "end_s": seg["end_s"]}]})
        assert r.json()["eval"]["recall"] == 1.0

        # Retune with a nonsense key is rejected, a real one is queued.
        assert c.post(f"/api/jobs/{job_id}/retune", json={"config": {"nope": 1}}).status_code == 400
        r = c.post(f"/api/jobs/{job_id}/retune", json={"config": {"segmentation": {"min_duration_s": 60}}, "skip_clips": True})
        assert r.status_code == 200
        d = _wait(lambda: (lambda j: j if j["status"] in ("done", "error") else None)(c.get(f"/api/jobs/{job_id}").json()))
        assert d["status"] == "done" and d["highlights"]["highlights"] == []

        assert c.delete(f"/api/jobs/{job_id}").json()["ok"]
        assert c.get(f"/api/jobs/{job_id}").status_code == 404


# ------------------------------------------------------------ Playwright --

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _chromium_exe() -> str | None:
    hits = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")
    return hits[0] if hits else None


@pytest.mark.slow
def test_ui_end_to_end(tmp_path: Path, synth_video: Path):
    pytest.importorskip("playwright")
    import uvicorn
    from playwright.sync_api import sync_playwright

    from tennishl.web import create_app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(tmp_path / "data"), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    _wait(lambda: server.started, timeout=20)
    base = f"http://127.0.0.1:{port}"
    shots = Path(os.environ.get("TENNISHL_SHOTS", tmp_path / "shots"))
    shots.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        kwargs = {"args": ["--no-sandbox"]}
        exe = _chromium_exe()
        if exe:
            kwargs["executable_path"] = exe
        browser = p.chromium.launch(**kwargs)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(base)
        page.fill("#path", str(synth_video))
        page.check("#noball")
        page.click("#go")
        page.wait_for_url("**/#/job/*", timeout=15000)
        page.wait_for_selector(".pill.done", timeout=240000)
        page.screenshot(path=str(shots / "job_done.png"), full_page=True)

        cards = page.locator(".card")
        n = cards.count()
        assert n >= 2
        assert page.locator("#tl").is_visible()

        # Untick the first card and render; the montage link must appear.
        cards.nth(0).locator("input").click()
        page.click("#render")
        page.wait_for_selector(".pill.running, .pill.queued", timeout=10000)
        page.wait_for_selector(".pill.done", timeout=240000)
        assert page.locator("a", has_text="Ladda ner montage").count() == 1

        # Label two points via the keyboard and save; eval numbers must show.
        page.click("#label-toggle")
        seg = page.evaluate("() => job.analysis.segmentation.segments.slice(0, 2)")
        for s in seg:
            page.evaluate(f"() => {{ document.querySelector('#video').currentTime = {s['start_s']}; }}")
            page.keyboard.press("i")
            page.evaluate(f"() => {{ document.querySelector('#video').currentTime = {s['end_s']}; }}")
            page.keyboard.press("o")
        assert page.locator("#truthlist .pill").count() == 2
        page.click("#truth-save")
        page.wait_for_selector("#eval .stat", timeout=10000)
        assert "recall" in page.locator("#eval").inner_text()
        page.screenshot(path=str(shots / "job_labeled.png"), full_page=True)

        # Retune from the UI, then the list view.
        page.click("#tune summary")
        page.fill("input[data-key='segmentation.min_duration_s']", "60")
        page.click("#retune")
        page.wait_for_selector(".pill.done", timeout=240000)
        page.wait_for_function("() => document.querySelectorAll('.card').length === 0", timeout=15000)
        page.goto(base + "/#/")
        page.wait_for_selector("#jobs tbody tr.link", timeout=10000)
        page.screenshot(path=str(shots / "list.png"), full_page=True)
        browser.close()

    server.should_exit = True

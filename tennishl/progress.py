"""Progress reporting.

Text mode draws a single updating line per stage. JSON mode emits one object
per line on stderr so a GUI (or the future app) can drive a real progress bar.
"""

from __future__ import annotations

import json
import sys
import time


from typing import Callable, Optional

EventFn = Callable[[dict], None]


class Progress:
    """``on_event`` receives every progress/log event as a dict - the web UI
    hooks in there; the CLI draws to stderr."""

    def __init__(
        self,
        *,
        json_lines: bool = False,
        quiet: bool = False,
        on_event: Optional[EventFn] = None,
    ) -> None:
        self.json_lines = json_lines
        self.quiet = quiet
        self.on_event = on_event
        self._stage = ""
        self._last_draw = 0.0
        self._stage_start = 0.0

    def stage(self, name: str) -> None:
        self._finish_line()
        self._stage = name
        self._stage_start = time.time()
        self._emit(0.0, force=True)

    def update(self, done: int, total: int) -> None:
        frac = min(1.0, done / total) if total > 0 else 0.0
        self._emit(frac)

    def done(self, message: str | None = None) -> None:
        self._emit(1.0, force=True)
        self._finish_line()
        if message:
            self.log(message)

    def log(self, message: str) -> None:
        if self.on_event:
            self.on_event({"type": "log", "message": message})
        if self.quiet:
            return
        if self.json_lines:
            print(json.dumps({"type": "log", "message": message}), file=sys.stderr, flush=True)
        else:
            self._finish_line()
            print(message, file=sys.stderr, flush=True)

    # ------------------------------------------------------------------
    def _emit(self, frac: float, *, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_draw < 0.15:
            return
        self._last_draw = now
        if self.on_event:
            self.on_event({"type": "progress", "stage": self._stage, "fraction": round(frac, 4)})
        if self.quiet:
            return
        if self.json_lines:
            print(
                json.dumps({"type": "progress", "stage": self._stage, "fraction": round(frac, 4)}),
                file=sys.stderr,
                flush=True,
            )
        else:
            elapsed = now - self._stage_start
            bar_w = 28
            filled = int(round(bar_w * frac))
            bar = "#" * filled + "-" * (bar_w - filled)
            sys.stderr.write(f"\r{self._stage:<26} [{bar}] {frac*100:5.1f}%  {elapsed:5.1f}s")
            sys.stderr.flush()

    def _finish_line(self) -> None:
        if self.quiet or self.json_lines or not self._stage:
            return
        sys.stderr.write("\n")
        sys.stderr.flush()
        self._stage = ""

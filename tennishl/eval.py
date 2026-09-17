"""Compare detected segments with hand-labelled ground truth.

Truth format (same as synth.py writes):
    [{"start_s": 6.0, "end_s": 10.2, "shots": 3}, ...]   ("shots" optional)

A detection matches a truth point when their overlap is at least half of the
shorter of the two. That is deliberately loose: the question is "did we find
this point", boundary precision is reported separately.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


@dataclass
class EvalResult:
    n_truth: int
    n_detected: int
    matched: int
    recall: float
    precision: float
    mean_start_error_s: float
    mean_end_error_s: float
    missed: list[dict] = field(default_factory=list)
    false_positives: list[dict] = field(default_factory=list)
    pairs: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "n_truth": self.n_truth,
            "n_detected": self.n_detected,
            "matched": self.matched,
            "recall": round(self.recall, 3),
            "precision": round(self.precision, 3),
            "mean_start_error_s": round(self.mean_start_error_s, 2),
            "mean_end_error_s": round(self.mean_end_error_s, 2),
            "missed": self.missed,
            "false_positives": self.false_positives,
            "pairs": self.pairs,
        }


def _overlap(a: dict, b: dict) -> float:
    return max(0.0, min(a["end_s"], b["end_s"]) - max(a["start_s"], b["start_s"]))


def evaluate(truth: Sequence[dict], detected: Sequence[dict]) -> EvalResult:
    truth = [dict(t) for t in truth]
    detected = [dict(d) for d in detected]
    used: set[int] = set()
    pairs: list[dict] = []
    missed: list[dict] = []

    for t in truth:
        best_j, best_ov = -1, 0.0
        for j, d in enumerate(detected):
            if j in used:
                continue
            ov = _overlap(t, d)
            shorter = max(1e-6, min(t["end_s"] - t["start_s"], d["end_s"] - d["start_s"]))
            if ov / shorter >= 0.5 and ov > best_ov:
                best_j, best_ov = j, ov
        if best_j < 0:
            missed.append({"start_s": t["start_s"], "end_s": t["end_s"]})
            continue
        used.add(best_j)
        d = detected[best_j]
        pairs.append(
            {
                "truth": {"start_s": t["start_s"], "end_s": t["end_s"]},
                "detected": {"start_s": d["start_s"], "end_s": d["end_s"]},
                "start_error_s": round(d["start_s"] - t["start_s"], 2),
                "end_error_s": round(d["end_s"] - t["end_s"], 2),
            }
        )

    fps = [
        {"start_s": d["start_s"], "end_s": d["end_s"]}
        for j, d in enumerate(detected)
        if j not in used
    ]
    matched = len(pairs)
    recall = matched / len(truth) if truth else 0.0
    precision = matched / len(detected) if detected else 0.0
    mse = sum(abs(p["start_error_s"]) for p in pairs) / matched if matched else 0.0
    mee = sum(abs(p["end_error_s"]) for p in pairs) / matched if matched else 0.0
    return EvalResult(
        n_truth=len(truth),
        n_detected=len(detected),
        matched=matched,
        recall=recall,
        precision=precision,
        mean_start_error_s=mse,
        mean_end_error_s=mee,
        missed=missed,
        false_positives=fps,
        pairs=pairs,
    )


def evaluate_files(analysis_json: str | Path, truth_json: str | Path) -> EvalResult:
    analysis = json.loads(Path(analysis_json).read_text(encoding="utf-8"))
    truth = json.loads(Path(truth_json).read_text(encoding="utf-8"))
    if isinstance(truth, dict):
        truth = truth.get("points", [])
    return evaluate(truth, analysis["segmentation"]["segments"])

"""Module llama_bench — schedule periodic llama-bench runs + track drift (R&D #8.4).

Why : LLM rig health degrades silently. Driver updates, thermal aging, model
re-quantization can shave 5-15% off your tok/s without you noticing. By
running llama-bench on a fixed test set nightly, we catch regressions
within 24h.

Storage : `llama_bench_runs` SQLite table.
  Columns : ts, build_commit, model, test, n_prompt, n_gen, avg_ts, stddev_ts

llama-bench output schema (--output json) :
  [
    {"build_commit": "abc", "model_filename": "...",
     "model_type": "qwen2", "n_params": 7400000000,
     "n_threads": 8, "n_gpu_layers": 99, "n_batch": 2048,
     "test": "pp512", "n_prompt": 512, "n_gen": 0,
     "avg_ts": 256.7, "stddev_ts": 1.5,
     "avg_ns": 12345678, "stddev_ns": 12345}, ...
  ]
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Optional


NAME = "llama_bench"

_DEFAULT_PATH_HINTS = [
    "/home/olivier/llama.cpp/build/bin/llama-bench",
    "/usr/local/bin/llama-bench",
    "/usr/bin/llama-bench",
]


def find_binary() -> Optional[str]:
    """Find the llama-bench binary in standard paths or via $PATH."""
    for p in _DEFAULT_PATH_HINTS:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    # Fall back to PATH
    try:
        r = subprocess.run(["which", "llama-bench"], capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    return None


def parse_output(json_text: str) -> list:
    """Parse llama-bench --output json into a list of normalized run rows.

    Returns list of dicts :
      {build_commit, model, test, n_prompt, n_gen, avg_ts, stddev_ts}
    """
    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            out.append({
                "build_commit": str(entry.get("build_commit", ""))[:40],
                "model": os.path.basename(str(entry.get("model_filename", ""))),
                "test": str(entry.get("test", "")),
                "n_prompt": int(entry.get("n_prompt", 0)),
                "n_gen": int(entry.get("n_gen", 0)),
                "avg_ts": float(entry.get("avg_ts", 0.0)),
                "stddev_ts": float(entry.get("stddev_ts", 0.0)),
            })
        except (ValueError, TypeError):
            continue
    return out


def run_bench(binary: str, model: str, p: int = 512, n: int = 128,
              repetitions: int = 3, timeout_s: int = 300) -> list:
    """Run llama-bench once on `model` with prompt size p / gen size n.

    Returns parsed run rows (list). Empty list on failure.
    Caller is responsible for catching GPU contention (already-running
    LLM server holding VRAM).
    """
    if not os.path.isfile(model):
        return []
    try:
        r = subprocess.run(
            [binary, "-m", model, "-p", str(p), "-n", str(n),
             "-r", str(repetitions), "-o", "json"],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []
    if r.returncode != 0:
        return []
    return parse_output(r.stdout)


def detect_regression(runs: list, threshold_pct: float = 5.0) -> Optional[dict]:
    """Compare the latest run's avg_ts vs the median of the prior N (default 7).

    Returns {regression: bool, delta_pct, latest_ts, baseline_ts} or None if not enough data.
    """
    if not runs or len(runs) < 3:
        return None
    # `runs` assumed sorted by ts ASC. Last = latest. Prior = up to 7 before.
    latest = runs[-1]
    prior = runs[-8:-1] if len(runs) >= 8 else runs[:-1]
    if not prior:
        return None
    baseline_ts_vals = sorted([r["avg_ts"] for r in prior])
    median = baseline_ts_vals[len(baseline_ts_vals) // 2]
    if median <= 0:
        return None
    delta_pct = (latest["avg_ts"] - median) / median * 100
    return {
        "regression": delta_pct < -threshold_pct,
        "delta_pct": round(delta_pct, 2),
        "latest_ts": latest["avg_ts"],
        "baseline_median_ts": median,
        "samples_compared": len(prior),
    }

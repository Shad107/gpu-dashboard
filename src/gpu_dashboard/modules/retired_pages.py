"""Module retired_pages — Retired-page trend (R&D #25.1).

The shipped ECC remap module (R&D #17.1) targets Ampere row-remap,
which is the Ampere-and-newer mechanism for retiring bad GDDR rows.
But Pascal / Volta / Maxwell — including the huge used-card cohort
(GTX 1080 Ti, Titan V, Tesla P40, P100) — use the older
"retired pages" model. NVML exposes them via :

  nvidia-smi --query-retired-pages=
    gpu_uuid,retired_pages.address,retired_pages.cause,
    retired_pages.timestamp --format=csv

Two cause buckets :
  - SBE  (Single-Bit Error)  — transient, ECC corrected
  - DBE  (Double-Bit Error)  — silicon defect, can't be corrected

A growing SBE count is a leading indicator of memory degradation ;
any DBE count > 0 is a near-RMA situation.

This module baselines per-GPU on first observation, tracks deltas
between calls, and emits verdicts.

stdlib only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Optional


NAME = "retired_pages"


_BASELINE_PATH = "~/.config/gpu-dashboard/retired_pages_baseline.json"


def baseline_path() -> str:
    return os.path.expanduser(_BASELINE_PATH)


def load_baseline() -> dict:
    p = baseline_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_baseline(data: dict) -> None:
    p = baseline_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=2)


def query_retired_pages(timeout: float = 2.0) -> Optional[list[dict]]:
    """Return list of retired-page entries, or None on driver error.
    Empty list = supported but nothing retired."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-retired-pages=gpu_uuid,retired_pages.address,"
             "retired_pages.cause,retired_pages.timestamp",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        # Card may not support the query (newer architectures use ECC remap)
        return None
    return parse_retired_csv(r.stdout)


def parse_retired_csv(text: str) -> list[dict]:
    """Parse the CSV. Empty rows / 'No data' lines → empty list."""
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("no "):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0].startswith("GPU-"):
            continue
        out.append({
            "gpu_uuid": parts[0],
            "address": parts[1],
            "cause": parts[2],
            "timestamp": parts[3] if len(parts) > 3 else "",
        })
    return out


def aggregate_by_cause(entries: list[dict]) -> dict:
    """Per-GPU bucket counts. Returns
    {uuid: {sbe: int, dbe: int, total: int, entries: [...]}}.
    """
    out: dict = {}
    for e in entries:
        uuid = e["gpu_uuid"]
        bucket = out.setdefault(uuid, {"sbe": 0, "dbe": 0,
                                        "total": 0, "entries": []})
        cause = e.get("cause", "").lower()
        if "single" in cause or "sbe" in cause:
            bucket["sbe"] += 1
        elif "double" in cause or "dbe" in cause:
            bucket["dbe"] += 1
        bucket["total"] += 1
        bucket["entries"].append(e)
    return out


def classify(by_gpu: dict, baseline: dict,
              now_ts: Optional[float] = None) -> dict:
    """Per-GPU verdict + summary."""
    if now_ts is None:
        now_ts = time.time()
    per_gpu: list = []
    new_baseline: dict = dict(baseline)
    worst_severity = "info"
    rank = {"info": 0, "warn": 1, "critical": 2}
    for uuid, bucket in by_gpu.items():
        base = baseline.get(uuid)
        if base is None:
            new_baseline[uuid] = {
                "first_seen_ts": int(now_ts),
                "sbe": bucket["sbe"],
                "dbe": bucket["dbe"],
            }
            delta_sbe = 0
            delta_dbe = 0
            first_seen = True
        else:
            delta_sbe = max(0, bucket["sbe"] - base.get("sbe", 0))
            delta_dbe = max(0, bucket["dbe"] - base.get("dbe", 0))
            first_seen = False
        verdict = _verdict_for_gpu(bucket, delta_sbe, delta_dbe)
        if rank.get(verdict["severity"], 0) > rank.get(worst_severity, 0):
            worst_severity = verdict["severity"]
        per_gpu.append({
            "uuid": uuid,
            "sbe": bucket["sbe"],
            "dbe": bucket["dbe"],
            "total": bucket["total"],
            "delta_sbe": delta_sbe,
            "delta_dbe": delta_dbe,
            "first_seen": first_seen,
            "verdict": verdict,
        })
    return {"per_gpu": per_gpu,
            "new_baseline": new_baseline,
            "worst_severity": worst_severity}


def _verdict_for_gpu(bucket: dict, delta_sbe: int, delta_dbe: int) -> dict:
    """One GPU's verdict."""
    if bucket["dbe"] > 0 or delta_dbe > 0:
        return {"severity": "critical",
                "label": "dbe_present",
                "reason": (f"{bucket['dbe']} double-bit error(s) retired. "
                           "Silicon defect — near-RMA situation."),
                "recommendation": "Plan for RMA. Run memtest before."}
    if delta_sbe >= 5:
        return {"severity": "warn",
                "label": "sbe_growth",
                "reason": (f"+{delta_sbe} new single-bit errors since baseline. "
                           "Memory is degrading."),
                "recommendation": ("Monitor weekly. If growth >10/week, "
                                    "consider RMA.")}
    if bucket["sbe"] > 0 and delta_sbe == 0:
        return {"severity": "info",
                "label": "sbe_stable",
                "reason": (f"{bucket['sbe']} SBE recorded but stable since "
                           "baseline. ECC is doing its job."),
                "recommendation": ""}
    if bucket["sbe"] > 0:
        return {"severity": "info",
                "label": "sbe_growing",
                "reason": (f"+{delta_sbe} new SBE — low rate, watch for "
                           "acceleration."),
                "recommendation": ""}
    return {"severity": "info",
            "label": "clean",
            "reason": "No retired pages.",
            "recommendation": ""}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    entries = query_retired_pages()
    if entries is None:
        return {
            "ok": False,
            "reason": ("nvidia-smi unreachable, or this driver/card does "
                        "not support --query-retired-pages (newer cards "
                        "use row-remap — see R&D #17.1)."),
            "supported": False,
            "per_gpu": [],
        }
    by_gpu = aggregate_by_cause(entries)
    baseline = load_baseline()
    cls = classify(by_gpu, baseline)
    if cls["new_baseline"] != baseline:
        save_baseline(cls["new_baseline"])
    return {
        "ok": True,
        "supported": True,
        "per_gpu": cls["per_gpu"],
        "worst_severity": cls["worst_severity"],
        "total_entries": len(entries),
    }

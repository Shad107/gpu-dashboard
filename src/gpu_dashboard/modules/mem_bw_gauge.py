"""Module mem_bw_gauge — Memory-bandwidth saturation gauge (R&D #26.8).

`nvidia-smi --query-gpu=utilization.memory` is the single most
mis-read metric in the NVIDIA telemetry surface. It does NOT
report memory bandwidth usage. It reports the percentage of time
during which the memory controller was reading or writing — which
saturates at 100% well before actual bandwidth is exhausted.

The signal we actually want is the *ratio* between compute and
memory busy-time : if mem >> gpu, the workload is bandwidth-bound
(adding compute won't help — quantize more, batch smaller, switch
to a smaller model). If gpu >> mem, the workload is compute-bound
(memory speed is fine — add more compute).

This module samples utilization.gpu and utilization.memory over a
short window, computes the ratio, and pairs the verdict with the
shipped warmup profile (#19.4) so the recommendation can be
workload-specific.

stdlib only.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from typing import Optional


NAME = "mem_bw_gauge"

# Honored by collection_profile_audit (hardening #2): this module
# intentionally samples nvidia-smi over a ~2.5 s window for the
# gpu/mem ratio. Single-shot would race nvidia-smi's own poll
# interval. Excluded from the per-module budget; still tracked in
# top_slowest.
EXPECTED_SLOW = True


def query_utilization_pair(timeout: float = 2.0) -> Optional[dict]:
    """Single sample of {gpu_util, mem_util} from nvidia-smi."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,utilization.gpu,utilization.memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    rows: list[dict] = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        try:
            rows.append({
                "index": int(parts[0]),
                "gpu_util_pct": int(parts[1]),
                "mem_util_pct": int(parts[2]),
            })
        except ValueError:
            continue
    return {"rows": rows, "ts": time.time()} if rows else None


def sample_window(n: int = 5, interval_s: float = 0.5) -> dict:
    """Collect N samples spaced interval_s apart. Returns per-GPU
    aggregated {gpu_util_mean, mem_util_mean, ratio_mem_over_gpu,
    sample_count}."""
    by_index: dict = {}
    sample_count = 0
    for _ in range(max(1, n)):
        s = query_utilization_pair()
        if s is None:
            time.sleep(interval_s)
            continue
        for row in s["rows"]:
            entry = by_index.setdefault(row["index"],
                                          {"gpu": [], "mem": []})
            entry["gpu"].append(row["gpu_util_pct"])
            entry["mem"].append(row["mem_util_pct"])
        sample_count += 1
        time.sleep(interval_s)
    aggregates: list[dict] = []
    for idx, e in by_index.items():
        if not e["gpu"]:
            continue
        gpu_mean = sum(e["gpu"]) / len(e["gpu"])
        mem_mean = sum(e["mem"]) / len(e["mem"])
        ratio = (mem_mean / gpu_mean) if gpu_mean > 0 else None
        aggregates.append({
            "index": idx,
            "gpu_util_mean": round(gpu_mean, 1),
            "mem_util_mean": round(mem_mean, 1),
            "ratio_mem_over_gpu": (round(ratio, 2)
                                    if ratio is not None else None),
            "sample_count": len(e["gpu"]),
        })
    return {"per_gpu": aggregates, "total_samples": sample_count}


def classify(agg: dict) -> dict:
    """Per-GPU verdict.
       - idle              both util < 5%
       - balanced          ratio 0.8 — 1.25
       - compute_bound     ratio < 0.8 (compute > mem)
       - bandwidth_bound   ratio > 1.25 (mem > compute)
       - undetermined      gpu_util_mean = 0 ; cannot divide
    """
    gpu = agg.get("gpu_util_mean", 0)
    mem = agg.get("mem_util_mean", 0)
    ratio = agg.get("ratio_mem_over_gpu")
    if gpu < 5 and mem < 5:
        return {"verdict": "idle",
                "reason": ("GPU is idle. Nothing to classify yet — "
                           "start a workload first."),
                "recommendation": ""}
    if ratio is None:
        return {"verdict": "undetermined",
                "reason": ("Compute utilization is zero but memory is "
                           "active — race condition or display-only GPU."),
                "recommendation": ""}
    if ratio > 1.25:
        return {"verdict": "bandwidth_bound",
                "reason": (f"Memory controller busy {mem:.0f}% vs compute "
                           f"{gpu:.0f}% (ratio {ratio:.2f}). Workload is "
                           "memory-bandwidth bound."),
                "recommendation": ("Use a smaller quant (Q4 / Q5 instead "
                                    "of Q8), or a smaller model. "
                                    "Adding compute headroom won't help.")}
    if ratio < 0.8:
        return {"verdict": "compute_bound",
                "reason": (f"Compute busy {gpu:.0f}% vs memory {mem:.0f}% "
                           f"(ratio {ratio:.2f}). Workload is compute-bound."),
                "recommendation": ("Memory speed isn't the bottleneck. "
                                    "Use higher-precision quants if quality "
                                    "matters more than throughput, or chain "
                                    "more inference requests in parallel.")}
    return {"verdict": "balanced",
            "reason": (f"Compute {gpu:.0f}% vs memory {mem:.0f}% "
                       f"(ratio {ratio:.2f}). Workload is balanced."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    n_samples = 5
    interval = 0.5
    if cfg:
        try:
            n_samples = max(1, min(20, int(cfg.get("BW_GAUGE_SAMPLES", "5"))))
        except (ValueError, TypeError):
            pass
        try:
            interval = max(0.1, min(2.0,
                            float(cfg.get("BW_GAUGE_INTERVAL_S", "0.5"))))
        except (ValueError, TypeError):
            pass
    window = sample_window(n=n_samples, interval_s=interval)
    if not window["per_gpu"]:
        return {"ok": False,
                "reason": "nvidia-smi unreachable.",
                "per_gpu": []}
    per_gpu: list = []
    for agg in window["per_gpu"]:
        verdict = classify(agg)
        per_gpu.append({**agg, "verdict": verdict})
    return {
        "ok": True,
        "per_gpu": per_gpu,
        "total_samples": window["total_samples"],
    }

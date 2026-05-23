"""Module proc_deep_state — /proc/driver/nvidia/gpus diff (R&D #23.6).

NVML exposes nvidia-smi visible fields, but several driver-internal
flags only live in /proc/driver/nvidia/gpus/<bdf>/information :

  - GPU Excluded   ("Yes" = card flagged for removal by driver — RMA)
  - GPU Firmware   (current GSP firmware version, distinct from driver)
  - Video BIOS     (VBIOS revision — also in nvidia-smi but cross-check)
  - DMA Size       (BAR1 / addressable space)
  - IRQ            (shifts unexpectedly = PCI re-enumeration)

This module reads each GPU's info block, baselines on first
observation, and flags drift on every subsequent call. Rounds out
the GSP/XID/reset-counter health trio (#21.3/#7/#22.1).

stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional


NAME = "proc_deep_state"


_PROC_ROOT = "/proc/driver/nvidia/gpus"
_BASELINE_PATH = "~/.config/gpu-dashboard/proc_deep_baseline.json"


def proc_root() -> str:
    return _PROC_ROOT


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


def list_gpu_dirs(root: Optional[str] = None) -> list[str]:
    """List /proc/driver/nvidia/gpus/<bdf> dirs."""
    p = root or proc_root()
    if not os.path.isdir(p):
        return []
    out: list[str] = []
    try:
        for name in sorted(os.listdir(p)):
            if re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.\d", name):
                out.append(os.path.join(p, name))
    except OSError:
        return []
    return out


_KEY_RE = re.compile(r"^\s*([^:]+?)\s*:\s*(.*?)\s*$")


def parse_information(text: str) -> dict:
    """Parse the 'key: value' lines from a GPU's information block."""
    out: dict = {}
    for line in text.splitlines():
        m = _KEY_RE.match(line)
        if not m:
            continue
        k, v = m.group(1).strip(), m.group(2).strip()
        # Normalize whitespace/tabs that nvidia uses
        k = re.sub(r"\s+", " ", k)
        v = re.sub(r"\s+", " ", v)
        out[k] = v
    return out


def read_gpu_information(gpu_dir: str) -> Optional[dict]:
    """Read and parse /proc/driver/nvidia/gpus/<bdf>/information."""
    p = os.path.join(gpu_dir, "information")
    try:
        with open(p) as f:
            text = f.read()
    except OSError:
        return None
    return parse_information(text)


# Fields we treat as "stable" — drift on these is suspicious.
TRACKED_FIELDS = [
    "Model", "GPU UUID", "Video BIOS", "Bus Type", "DMA Size",
    "Bus Location", "GPU Firmware", "GPU Excluded",
]


def detect_drift(baseline_entry: dict, current_entry: dict) -> list[dict]:
    """Compare two information dicts. Returns list of {field, before, after}."""
    out: list[dict] = []
    for f in TRACKED_FIELDS:
        b = baseline_entry.get(f)
        c = current_entry.get(f)
        if b is None and c is None:
            continue
        if b != c:
            out.append({"field": f, "before": b, "after": c})
    return out


def classify(reports: list[dict]) -> dict:
    """Return {verdict, reason, severity} based on per-GPU drift reports."""
    if not reports:
        return {"verdict": "no_gpus",
                "reason": "No NVIDIA GPUs found in /proc/driver/nvidia/gpus.",
                "severity": "info"}
    any_excluded = any(r.get("excluded") for r in reports)
    any_firmware_drift = any(
        any(d["field"] == "GPU Firmware" for d in r["drift"])
        for r in reports
    )
    any_vbios_drift = any(
        any(d["field"] == "Video BIOS" for d in r["drift"])
        for r in reports
    )
    any_drift = any(r["drift"] for r in reports)
    if any_excluded:
        return {"verdict": "excluded",
                "reason": ("Driver marked at least one GPU as 'GPU Excluded' "
                           "— hardware fault / RMA candidate."),
                "severity": "critical"}
    if any_firmware_drift:
        return {"verdict": "firmware_drift",
                "reason": ("GSP firmware version changed since baseline "
                           "(driver upgrade or silent re-flash)."),
                "severity": "warn"}
    if any_vbios_drift:
        return {"verdict": "vbios_drift",
                "reason": ("VBIOS revision changed since baseline (intentional "
                           "flash or vendor-tool surprise)."),
                "severity": "warn"}
    if any_drift:
        return {"verdict": "minor_drift",
                "reason": "Non-critical field drifted (IRQ, DMA size, etc.).",
                "severity": "info"}
    return {"verdict": "clean",
            "reason": "All tracked procfs fields match baseline.",
            "severity": "info"}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    gpu_dirs = list_gpu_dirs()
    baseline = load_baseline()
    reports: list[dict] = []
    new_baseline_entries: dict = {}
    for d in gpu_dirs:
        bdf = os.path.basename(d)
        info = read_gpu_information(d)
        if info is None:
            continue
        uuid = info.get("GPU UUID") or bdf
        base = baseline.get(uuid)
        if base is None:
            new_baseline_entries[uuid] = {**info,
                                            "first_seen_ts": int(time.time())}
            drift: list[dict] = []
            first_seen = True
        else:
            drift = detect_drift(base, info)
            first_seen = False
        excluded = info.get("GPU Excluded", "No").strip().lower() == "yes"
        reports.append({
            "bdf": bdf,
            "uuid": uuid,
            "model": info.get("Model", "?"),
            "video_bios": info.get("Video BIOS", "?"),
            "gpu_firmware": info.get("GPU Firmware", "?"),
            "dma_size": info.get("DMA Size", "?"),
            "irq": info.get("IRQ", "?"),
            "excluded": excluded,
            "first_seen": first_seen,
            "drift": drift,
        })
    if new_baseline_entries:
        baseline.update(new_baseline_entries)
        save_baseline(baseline)
    verdict = classify(reports)
    return {
        "ok": True,
        "gpus": reports,
        "gpu_count": len(reports),
        "drift_count": sum(1 for r in reports if r["drift"]),
        "excluded_count": sum(1 for r in reports if r["excluded"]),
        "verdict": verdict,
    }

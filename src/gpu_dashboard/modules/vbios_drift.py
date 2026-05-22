"""Module vbios_drift — VBIOS revision + ROM hash drift tracker (R&D #20.2).

Used-card resale market (especially RTX 3090s from mining farms) often
hides relabeled or flashed VBIOSes. Even on first-hand cards, an
NVIDIA driver update or vendor tool can silently flash the VBIOS —
sometimes downgrading features (lower power limit cap) or breaking
overclocks.

This module records a baseline per GPU UUID on first run :
  - vbios_version  (from nvidia-smi --query-gpu=vbios_version)
  - rom_sha256     (sha256 of /sys/bus/pci/devices/<bdf>/rom if readable)

On every subsequent call it compares current readings to the baseline
and flags any drift. The baseline lives at
~/.config/gpu-dashboard/vbios_baseline.json — user can re-baseline
intentionally after a known-good flash.

stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from typing import Optional


NAME = "vbios_drift"


_BASELINE_PATH = "~/.config/gpu-dashboard/vbios_baseline.json"


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


def current_vbios(timeout: float = 2.0) -> list[dict]:
    """`nvidia-smi --query-gpu=uuid,vbios_version,name,pci.bus_id`."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=uuid,vbios_version,name,pci.bus_id",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if r.returncode != 0:
        return []
    out: list[dict] = []
    for line in r.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        out.append({
            "uuid": parts[0],
            "vbios_version": parts[1],
            "name": parts[2],
            "bdf": parts[3],
        })
    return out


def hash_rom(bdf: str) -> Optional[str]:
    """Compute sha256 of /sys/bus/pci/devices/<bdf>/rom. Requires the
    user to have first done `echo 1 > .../rom`. Returns None on
    permission error / unreadable / empty file (the typical case for
    non-root users)."""
    p = f"/sys/bus/pci/devices/{bdf}/rom"
    if not os.path.exists(p):
        return None
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            chunk = f.read(64 * 1024)
            if not chunk:
                return None
            while chunk:
                h.update(chunk)
                chunk = f.read(64 * 1024)
        return h.hexdigest()
    except (OSError, PermissionError):
        return None


def detect_drift(baseline: dict, current_gpus: list[dict]) -> list[dict]:
    """For each currently-seen GPU, compare against the baseline and
    return a per-GPU drift report. New GPUs (not in baseline yet) get
    drift=False with 'first_seen' reason."""
    reports: list[dict] = []
    for cur in current_gpus:
        uuid = cur["uuid"]
        base = baseline.get(uuid)
        cur_rom = hash_rom(cur["bdf"])
        rec = {
            "uuid": uuid,
            "name": cur["name"],
            "bdf": cur["bdf"],
            "current_vbios": cur["vbios_version"],
            "current_rom_sha256": cur_rom,
            "baseline_vbios": (base["vbios_version"] if base else None),
            "baseline_rom_sha256": (base.get("rom_sha256") if base else None),
            "drift": False,
            "reasons": [],
        }
        if base is None:
            rec["reasons"].append("first_seen")
            reports.append(rec)
            continue
        if base["vbios_version"] != cur["vbios_version"]:
            rec["drift"] = True
            rec["reasons"].append(
                f"vbios changed : {base['vbios_version']} → {cur['vbios_version']}")
        if (base.get("rom_sha256") and cur_rom
                and base["rom_sha256"] != cur_rom):
            rec["drift"] = True
            rec["reasons"].append("rom sha256 changed")
        if not rec["reasons"]:
            rec["reasons"].append("ok")
        reports.append(rec)
    return reports


def rebaseline(current_gpus: Optional[list[dict]] = None) -> dict:
    """Overwrite baseline with current state. Returns the new baseline."""
    if current_gpus is None:
        current_gpus = current_vbios()
    new: dict = {}
    for g in current_gpus:
        new[g["uuid"]] = {
            "vbios_version": g["vbios_version"],
            "name": g["name"],
            "bdf": g["bdf"],
            "rom_sha256": hash_rom(g["bdf"]),
        }
    save_baseline(new)
    return new


def status(cfg=None) -> dict:
    """Aggregate snapshot. Auto-baselines a GPU on its first observation
    (so 'first_seen' is one-shot — next call will compare against the
    just-saved baseline)."""
    current = current_vbios()
    if not current:
        return {
            "ok": False,
            "reason": "nvidia-smi unreachable",
            "gpus": [],
            "drift_count": 0,
        }
    baseline = load_baseline()
    reports = detect_drift(baseline, current)
    # Auto-baseline any 'first_seen' GPUs so we have a comparison point
    # next time. Do NOT overwrite an existing entry, even if it differs.
    updated = False
    for g in current:
        if g["uuid"] not in baseline:
            baseline[g["uuid"]] = {
                "vbios_version": g["vbios_version"],
                "name": g["name"],
                "bdf": g["bdf"],
                "rom_sha256": hash_rom(g["bdf"]),
            }
            updated = True
    if updated:
        save_baseline(baseline)
    return {
        "ok": True,
        "gpus": reports,
        "drift_count": sum(1 for r in reports if r["drift"]),
        "first_seen_count": sum(1 for r in reports if "first_seen" in r["reasons"]),
    }

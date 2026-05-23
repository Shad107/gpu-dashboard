"""Module driver_flavor — Open vs proprietary driver advisor (R&D #22.2).

Since R555 (June 2024), the open-kernel NVIDIA driver is the default
on Ubuntu 24.04, Fedora 41 and Arch. The proprietary driver still
exists and is required for older GPUs. Users who upgrade in place
often end up with the wrong flavor :

  - Pascal (GTX 10-series) installed open driver → no display, hangs
  - Turing+ stuck on legacy proprietary → missing newer features +
    slower NVENC/NVDEC pipeline integration in Wayland

This module :

  1. Reads /sys/module/nvidia/version  (running kmod version)
  2. Runs `modinfo nvidia`             (license + filename + flavor)
  3. Pulls GPU compute capability     (nvidia-smi --query-gpu=compute_cap)
  4. Cross-references the table : open requires sm_75+ (Turing)
  5. Returns advisory + the apt/dnf swap command

stdlib only.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Optional


NAME = "driver_flavor"


_MODULE_VERSION_PATH = "/sys/module/nvidia/version"


# compute_cap → architecture name + open driver support
ARCH_TABLE = {
    "5.0": ("Maxwell GM10x", False),
    "5.2": ("Maxwell GM20x", False),
    "5.3": ("Maxwell-Tegra", False),
    "6.0": ("Pascal GP100", False),
    "6.1": ("Pascal GP10x", False),
    "6.2": ("Pascal-Tegra", False),
    "7.0": ("Volta", False),  # open supports it from R555 only, treat conservative
    "7.2": ("Xavier", False),
    "7.5": ("Turing", True),
    "8.0": ("Ampere A100", True),
    "8.6": ("Ampere RTX 30xx", True),
    "8.7": ("Orin", True),
    "8.9": ("Ada Lovelace RTX 40xx", True),
    "9.0": ("Hopper H100", True),
    "10.0": ("Blackwell B100", True),
    "12.0": ("Blackwell GeForce RTX 50xx", True),
}


def read_module_version() -> Optional[str]:
    try:
        with open(_MODULE_VERSION_PATH) as f:
            return f.read().strip() or None
    except OSError:
        return None


def run_modinfo(module: str = "nvidia", timeout: float = 2.0) -> Optional[dict]:
    """Parse `modinfo nvidia` into a dict of keys."""
    if not shutil.which("modinfo"):
        return None
    try:
        r = subprocess.run(
            ["modinfo", module],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    out: dict = {}
    for line in r.stdout.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if k in out:
            # Append multi-valued
            existing = out[k]
            if isinstance(existing, list):
                existing.append(v)
            else:
                out[k] = [existing, v]
        else:
            out[k] = v
    return out


def detect_flavor(modinfo_data: Optional[dict]) -> str:
    """Return 'open' / 'proprietary' / 'unknown'. The proprietary blob
    declares license 'NVIDIA'. The open kernel modules declare
    'Dual MIT/GPL' or similar."""
    if not modinfo_data:
        return "unknown"
    lic = modinfo_data.get("license", "")
    if isinstance(lic, list):
        lic = lic[0]
    if "NVIDIA" in lic and "MIT" not in lic and "GPL" not in lic:
        return "proprietary"
    if "MIT" in lic or "GPL" in lic:
        return "open"
    # Some open module signed builds report a hybrid description
    desc = modinfo_data.get("description", "")
    if isinstance(desc, list):
        desc = desc[0]
    if "open" in desc.lower():
        return "open"
    return "unknown"


def list_gpu_compute_caps(timeout: float = 2.0) -> list[dict]:
    """Return [{index, name, compute_cap}] for each GPU."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,compute_cap",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if r.returncode != 0:
        return []
    out: list[dict] = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        out.append({
            "index": int(parts[0]),
            "name": parts[1],
            "compute_cap": parts[2],
        })
    return out


def classify(flavor: str, gpus: list[dict]) -> dict:
    """Return {verdict, reason, recommendation}."""
    if not gpus:
        return {"verdict": "unknown",
                "reason": "No GPUs visible to nvidia-smi.",
                "recommendation": ""}
    open_capable: list[dict] = []
    legacy_only: list[dict] = []
    for g in gpus:
        cap = g.get("compute_cap", "")
        arch_info = ARCH_TABLE.get(cap)
        if arch_info and arch_info[1]:
            open_capable.append({**g, "arch": arch_info[0]})
        else:
            arch_name = arch_info[0] if arch_info else f"unknown ({cap})"
            legacy_only.append({**g, "arch": arch_name})

    if flavor == "open" and legacy_only:
        return {
            "verdict": "wrong_flavor",
            "reason": (f"Open kernel driver is loaded but "
                       f"{len(legacy_only)} GPU(s) are Pascal-or-older "
                       "(no open support). Display + CUDA will be broken."),
            "recommendation": (
                "sudo apt purge nvidia-driver-open-* "
                "&& sudo apt install nvidia-driver-535"),
        }
    if flavor == "proprietary" and open_capable and not legacy_only:
        return {
            "verdict": "could_upgrade",
            "reason": (f"All {len(gpus)} GPU(s) support the open driver. "
                       "You're on the legacy proprietary build — switching "
                       "unlocks better Wayland integration + faster security "
                       "patches."),
            "recommendation": (
                "sudo apt install nvidia-driver-open-555  "
                "(verify package name on your distro)"),
        }
    if flavor == "open" and open_capable and not legacy_only:
        return {"verdict": "ok",
                "reason": "Open kernel driver loaded, all GPUs supported.",
                "recommendation": ""}
    if flavor == "proprietary" and legacy_only:
        return {"verdict": "ok",
                "reason": ("Proprietary driver loaded, required for "
                           f"{len(legacy_only)} legacy GPU(s)."),
                "recommendation": ""}
    if flavor == "unknown":
        return {"verdict": "unknown",
                "reason": "modinfo could not identify the flavor.",
                "recommendation": ""}
    return {"verdict": "mixed",
            "reason": (f"Mixed-arch system : {len(open_capable)} new + "
                       f"{len(legacy_only)} legacy. Stick with proprietary "
                       "driver until you retire the older card."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    version = read_module_version()
    mi = run_modinfo("nvidia")
    flavor = detect_flavor(mi)
    gpus = list_gpu_compute_caps()
    verdict = classify(flavor, gpus)
    # Annotate each GPU with arch name + open-support flag
    annotated: list[dict] = []
    for g in gpus:
        cap = g.get("compute_cap", "")
        arch_info = ARCH_TABLE.get(cap)
        annotated.append({
            **g,
            "arch": arch_info[0] if arch_info else "unknown",
            "open_supported": arch_info[1] if arch_info else False,
        })
    return {
        "ok": True,
        "kernel_module_version": version,
        "flavor": flavor,
        "modinfo_license": (mi.get("license") if mi else None),
        "modinfo_filename": (mi.get("filename") if mi else None),
        "gpus": annotated,
        "verdict": verdict,
    }

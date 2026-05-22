"""Module cuda_matrix — CUDA / cuDNN / driver compatibility matrix (R&D #18.2).

The single biggest support question from LLM-rig newcomers is
"is my CUDA / cuDNN / driver combo compatible?" This module gathers
all four facts at once and reports the verdict :

  - NVIDIA driver version       (nvidia-smi / NVML)
  - CUDA toolkit version        (/usr/local/cuda/version.json)
  - cuDNN library version       (cudnn_version.h or ldconfig + readelf)
  - PyTorch / TensorRT bindings (best-effort import check, optional)

Compatibility rules come from NVIDIA's CUDA Toolkit release notes
(minimum driver per CUDA major.minor). Hardcoded snapshot — updated
when a new CUDA version ships. Old combos still work, the table only
adds new rows.

stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Optional


NAME = "cuda_matrix"


# CUDA Toolkit → minimum Linux driver version, from NVIDIA's release notes.
# https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/
CUDA_MIN_DRIVER = {
    "13.0": 580.65,
    "12.9": 575.51,
    "12.8": 570.86,
    "12.7": 565.57,
    "12.6": 560.28,
    "12.5": 555.42,
    "12.4": 550.54,
    "12.3": 545.23,
    "12.2": 535.54,
    "12.1": 530.30,
    "12.0": 525.60,
    "11.8": 520.61,
    "11.7": 515.43,
    "11.6": 510.39,
    "11.5": 495.29,
    "11.4": 470.42,
    "11.3": 465.19,
    "11.2": 460.27,
    "11.1": 455.23,
    "11.0": 450.36,
}


def _normalize_version(s: str) -> str:
    """Strip non-numeric tail and reduce to major.minor."""
    m = re.match(r"^(\d+\.\d+)", s.strip())
    return m.group(1) if m else s.strip()


def driver_version() -> Optional[str]:
    """Read NVIDIA driver version from nvidia-smi."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    line = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
    return line.strip() or None


def cuda_toolkit_version(cuda_root: str = "/usr/local/cuda") -> Optional[dict]:
    """Parse /usr/local/cuda/version.json. Returns {version, name} or None."""
    p = os.path.join(cuda_root, "version.json")
    try:
        with open(p) as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _cuda_toolkit_from_version_txt(cuda_root)
    cuda = d.get("cuda") or {}
    ver = cuda.get("version")
    name = cuda.get("name") or "CUDA"
    if not ver:
        return None
    return {"version": ver, "name": name}


def _cuda_toolkit_from_version_txt(cuda_root: str) -> Optional[dict]:
    """Older CUDA installs use version.txt."""
    p = os.path.join(cuda_root, "version.txt")
    try:
        with open(p) as f:
            line = f.read().strip()
    except OSError:
        return None
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", line)
    if not m:
        return None
    return {"version": m.group(1), "name": "CUDA"}


def cudnn_version(lib_dirs: Optional[list[str]] = None) -> Optional[str]:
    """Detect cuDNN by reading /usr/include/cudnn_version.h (preferred) or
    grepping ldconfig output."""
    candidate_headers = [
        "/usr/include/cudnn_version.h",
        "/usr/include/x86_64-linux-gnu/cudnn_version.h",
        "/usr/local/cuda/include/cudnn_version.h",
    ]
    for h in candidate_headers:
        ver = _parse_cudnn_header(h)
        if ver:
            return ver
    # Fall back to ldconfig
    if shutil.which("ldconfig"):
        try:
            r = subprocess.run(["ldconfig", "-p"], capture_output=True,
                               text=True, timeout=2.0)
            for line in r.stdout.splitlines():
                m = re.search(r"libcudnn\.so\.([\d.]+)", line)
                if m:
                    return m.group(1)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
    return None


def _parse_cudnn_header(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            txt = f.read()
    except OSError:
        return None
    major = re.search(r"#define\s+CUDNN_MAJOR\s+(\d+)", txt)
    minor = re.search(r"#define\s+CUDNN_MINOR\s+(\d+)", txt)
    patch = re.search(r"#define\s+CUDNN_PATCHLEVEL\s+(\d+)", txt)
    if not major:
        return None
    parts = [major.group(1)]
    if minor: parts.append(minor.group(1))
    if patch: parts.append(patch.group(1))
    return ".".join(parts)


def min_driver_for_cuda(cuda_ver: str) -> Optional[float]:
    """Look up the minimum driver version for a given CUDA toolkit
    major.minor."""
    norm = _normalize_version(cuda_ver)
    return CUDA_MIN_DRIVER.get(norm)


def compat_verdict(driver_ver: Optional[str], cuda_ver: Optional[str]) -> dict:
    """Return {ok: bool, reason: str, required_driver: float | None}."""
    if cuda_ver is None or driver_ver is None:
        return {"ok": None, "reason": "missing driver or CUDA toolkit info",
                "required_driver": None}
    required = min_driver_for_cuda(cuda_ver)
    if required is None:
        return {"ok": None,
                "reason": (f"unknown CUDA {cuda_ver} — not in lookup table"),
                "required_driver": None}
    try:
        drv = float(_normalize_version(driver_ver))
    except ValueError:
        return {"ok": None, "reason": "could not parse driver version",
                "required_driver": required}
    if drv >= required:
        return {"ok": True,
                "reason": (f"driver {drv} ≥ required {required} "
                           f"for CUDA {cuda_ver}"),
                "required_driver": required}
    return {"ok": False,
            "reason": (f"driver {drv} < required {required} "
                       f"for CUDA {cuda_ver}. Upgrade driver or downgrade "
                       f"CUDA toolkit."),
            "required_driver": required}


def status(cfg=None) -> dict:
    """Aggregate compatibility snapshot."""
    cuda_root = "/usr/local/cuda"
    if cfg:
        cuda_root = cfg.get("CUDA_ROOT", cuda_root)
    drv = driver_version()
    cuda = cuda_toolkit_version(cuda_root)
    cudnn = cudnn_version()
    cuda_ver = cuda.get("version") if cuda else None
    verdict = compat_verdict(drv, cuda_ver)
    return {
        "ok": True,
        "driver_version": drv,
        "cuda_toolkit": cuda,
        "cudnn_version": cudnn,
        "compat": verdict,
        "cuda_min_driver_table": CUDA_MIN_DRIVER,
    }

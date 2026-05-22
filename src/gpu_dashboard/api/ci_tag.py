"""HTTP handler for the /api/ci-tag endpoint (R&D #12.5).

Self-hosted CI runners (GitHub Actions, Jenkins, GitLab) often need
GPU-aware labels for job scheduling. This endpoint exposes the current
machine's GPU capabilities in two formats consumable by registration
hooks :

  GET /api/ci-tag                 → text/plain key=value lines
  GET /api/ci-tag?fmt=json        → JSON

Returned labels :
  cuda=12.4              CUDA runtime version (from nvidia-smi --query-gpu=cuda_version)
  driver=560.35.03       driver version
  vram_free_gb=18        free VRAM in GiB (rounded)
  vram_total_gb=24       total VRAM
  gpu=3090               short GPU name (stripped of NVIDIA prefix)
  gpu_count=1            number of detected NVIDIA GPUs
  arch=ampere            best-effort GPU family (ada/ampere/turing/hopper)
  available=1            1 if vram_free_gb >= min_vram_gb (default 1), else 0

Optional gate :
  ?min_vram_gb=N         if specified, returns HTTP 503 when vram_free_gb < N
                          (useful in runner pre-job hooks : if 503, skip job)
"""
from __future__ import annotations

import subprocess
from typing import Optional, Tuple

from . import _core as _m


Response = Tuple[int, str]


def _gpu_card_snapshot(*args, **kw):
    return _m._gpu_card_snapshot(*args, **kw)


def _gpus_available(*args, **kw):
    return _m._gpus_available(*args, **kw)


def _short_gpu_name(name: str) -> str:
    """Normalize 'NVIDIA GeForce RTX 3090' to '3090', or 'A100' for datacenter."""
    if not name:
        return "unknown"
    s = name.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "").replace("RTX ", "")
    return s.lower().replace(" ", "-")


def _gpu_arch(name: str) -> str:
    """Best-effort GPU family inference from the marketing name."""
    n = (name or "").upper()
    # Datacenter
    if "H100" in n or "H200" in n: return "hopper"
    if "A100" in n or "A40" in n or "A30" in n: return "ampere"
    if "L40" in n or "L4" in n: return "ada"
    if "V100" in n: return "volta"
    if "T4" in n: return "turing"
    # GeForce / Quadro
    if "RTX 40" in n: return "ada"
    if "RTX 30" in n: return "ampere"
    if "RTX 20" in n or "TURING" in n: return "turing"
    if "GTX 16" in n: return "turing"
    if "GTX 10" in n: return "pascal"
    return "unknown"


def _query_cuda_driver() -> Tuple[Optional[str], Optional[str]]:
    """Fetch CUDA runtime + driver version via nvidia-smi.
    Returns (cuda_version, driver_version) — either may be None."""
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=cuda_version,driver_version",
             "--format=csv,noheader,nounits", "-i", "0"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode != 0:
            return None, None
        line = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            cuda = parts[0] if parts[0] and parts[0] != "[N/A]" else None
            drv = parts[1] if parts[1] and parts[1] != "[N/A]" else None
            return cuda, drv
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    return None, None


def _build_labels(min_vram_gb: float = 1.0) -> dict:
    """Aggregate all CI-relevant labels into a flat dict."""
    snap = _gpu_card_snapshot(gpu_index=0)
    gpus = []
    try:
        gpus = _gpus_available() or []
    except Exception:
        gpus = []

    alive = bool(snap and snap.get("alive"))
    name = snap.get("name", "") if alive else ""
    mem_total_mib = snap.get("mem_total_mib", 0) or 0
    mem_used_mib = snap.get("mem_used_mib", 0) or 0
    vram_total_gb = round(mem_total_mib / 1024, 1)
    vram_free_gb = round(max(0, mem_total_mib - mem_used_mib) / 1024, 1)

    cuda, driver = _query_cuda_driver()
    available = 1 if alive and vram_free_gb >= min_vram_gb else 0

    return {
        "alive": "1" if alive else "0",
        "gpu_count": str(len(gpus)),
        "gpu": _short_gpu_name(name) if alive else "none",
        "arch": _gpu_arch(name) if alive else "none",
        "cuda": cuda or "unknown",
        "driver": driver or "unknown",
        "vram_total_gb": str(vram_total_gb),
        "vram_free_gb": str(vram_free_gb),
        "available": str(available),
    }


def handle_ci_tag(ctx: dict, params: Optional[dict] = None) -> Response:
    """Emit GPU labels for CI runner registration.

    Query params :
      fmt = text (default, key=value per line) | json | flat (comma-joined)
      min_vram_gb = N → HTTP 503 if vram_free_gb < N (job-gate)
    """
    params = params or {}
    try:
        min_vram_gb = float(params.get("min_vram_gb", "0"))
    except (ValueError, TypeError):
        min_vram_gb = 0.0
    fmt = params.get("fmt", "text")

    labels = _build_labels(min_vram_gb=min_vram_gb)
    # Gate
    status = 200
    if min_vram_gb > 0 and labels["available"] == "0":
        status = 503

    if fmt == "json":
        import json as _json
        return status, _json.dumps({"ok": status == 200, "labels": labels})
    if fmt == "flat":
        return status, ",".join(f"{k}={v}" for k, v in labels.items()) + "\n"
    # default text : key=value per line
    return status, "\n".join(f"{k}={v}" for k, v in labels.items()) + "\n"

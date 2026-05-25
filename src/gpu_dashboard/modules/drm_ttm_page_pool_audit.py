"""Module drm_ttm_page_pool_audit — TTM page pool caps
posture (R&D #94.3).

The DRM TTM (Translation-Table Manager) page pool backs
every nvidia/amdgpu/i915 buffer object with host RAM
when VRAM is overcommitted. Three existing modules touch
nearby surface but never the TTM module parameters :

  * dri_debugfs_audit       — /sys/kernel/debug/dri/* name /
                              clients / framebuffer files
  * dma_buf_bufinfo_audit   — /sys/kernel/debug/dma_buf/bufinfo
  * vram_leak / vram_quota  — process-side NVML totals

The actual page-pool *state* (current page count, shrinker
activity) lives in /sys/kernel/debug/dri/*/ttm_page_pool
which is mode-700 (debugfs). The user-tunable *caps* live
in /sys/module/ttm/parameters/* which IS world-readable.
This audit fires on whether those caps are still at the
kernel-auto-tuning default vs explicitly user-set.

Reads :

  /sys/module/ttm/parameters/page_pool_size   max pool pages
                                              (0 = auto)
  /sys/module/ttm/parameters/pages_limit      max pages TTM
                                              can pin (0 =
                                              auto)
  /sys/module/ttm/parameters/dma32_pages_limit DMA32 cap
                                              (0 = auto)
  /proc/meminfo                              MemAvailable
                                              cross-ref

Verdicts (worst-first) :

  ttm_pool_uncapped  accent  page_pool_size = 0 AND
                             pages_limit = 0 — kernel
                             auto-sizes the pool based on
                             total RAM. Sane default on most
                             rigs but on a 24-GiB-class GPU
                             box with limited host RAM the
                             auto-size can eat into MemAvailable.
  ok                 ≥ 1 TTM parameter explicitly tuned.
  unknown            /sys/module/ttm absent (no TTM-using
                     GPU driver loaded, or kernel built
                     without DRM_TTM).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "drm_ttm_page_pool_audit"

DEFAULT_TTM_PARAMS = "/sys/module/ttm/parameters"
DEFAULT_PROC_MEMINFO = "/proc/meminfo"

_MEMAVAILABLE_RE = re.compile(
    r"^MemAvailable:\s*(\d+)\s*kB", re.MULTILINE)


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_mem_available_bytes(text: str) -> Optional[int]:
    if not text:
        return None
    m = _MEMAVAILABLE_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1)) * 1024
    except ValueError:
        return None


def read_ttm_params(root: str = DEFAULT_TTM_PARAMS) -> dict:
    return {
        "page_pool_size": _read_int(
            os.path.join(root, "page_pool_size")),
        "pages_limit": _read_int(
            os.path.join(root, "pages_limit")),
        "dma32_pages_limit": _read_int(
            os.path.join(root, "dma32_pages_limit")),
    }


def classify(params: dict,
             ttm_present: bool,
             mem_available: Optional[int]) -> dict:
    if not ttm_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/module/ttm absent — no TTM-using "
                    "DRM driver loaded (no nvidia / amdgpu / "
                    "radeon / i915) or kernel built without "
                    "DRM_TTM.")}

    pps = params.get("page_pool_size")
    pl = params.get("pages_limit")

    # accent — caps all at default
    if (pps is not None and pps == 0
            and pl is not None and pl == 0):
        mem_str = (
            f" ; MemAvailable = "
            f"{mem_available / 2**30:.1f} GiB"
            if mem_available else "")
        return {
            "verdict": "ttm_pool_uncapped",
            "reason": (
                "TTM page_pool_size = 0 AND pages_limit = 0 "
                "— pool is kernel-auto-sized. On VRAM-"
                f"overcommit scenarios the host mirror can "
                f"eat into MemAvailable{mem_str}.")}

    return {"verdict": "ok",
            "reason": (
                f"TTM caps explicitly set "
                f"(page_pool_size={pps}, pages_limit={pl}).")}


def status(config: Optional[dict] = None,
           ttm_params_root: str = DEFAULT_TTM_PARAMS,
           proc_meminfo: str = DEFAULT_PROC_MEMINFO) -> dict:
    ttm_present = os.path.isdir(ttm_params_root)
    params = (read_ttm_params(ttm_params_root)
              if ttm_present else {})
    mem_available = parse_mem_available_bytes(
        _read_text(proc_meminfo) or "")
    verdict = classify(params, ttm_present, mem_available)
    return {
        "ok": verdict["verdict"] == "ok",
        "ttm_present": ttm_present,
        "params": params,
        "mem_available": mem_available,
        "verdict": verdict,
    }

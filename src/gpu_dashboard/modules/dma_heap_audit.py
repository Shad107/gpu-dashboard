"""Module dma_heap_audit — dma-buf heap allocator audit
(R&D #74.2).

The kernel exposes named dma-buf heap allocators under
/sys/class/dma_heap/. Userspace opens the matching
/dev/dma_heap/<name> char device to obtain a dma-buf file
descriptor. Common heaps :

  system           pageable kernel allocator (always present
                     on modern kernels)
  system-uncached  same backing but with uncached mappings
                     (needed by some V4L2 / camera flows)
  cma              physically-contiguous Contiguous Memory
                     Allocator pool
  linux,cma        same as `cma` but DT-named

On a homelab GPU rig :

* NVIDIA + Wayland compositors increasingly rely on dma-buf
  heaps for zero-copy screencast / VAAPI bridges.
* A missing CMA heap silently forces fallback CPU copies and
  tanks zero-copy paths.
* A world-writable /dev/dma_heap/* is a privilege-escalation
  surface (memory-mapping ring-0-adjacent buffers without
  root).

Verdicts (priority order) :
  heaps_world_writable           ≥1 /dev/dma_heap/* has
                                   mode & 0o002 (world-write).
  cma_heap_missing_for_dma_buf   GPU detected (drm card +
                                   NVIDIA / amdgpu) but no
                                   CMA-style heap registered.
  only_system_heap_present       Only the "system" heap, no
                                   variants — informational.
  heap_perms_root_only           /dev/dma_heap/* exists but is
                                   0600 root (compositors that
                                   need it run unprivileged).
  ok                              dma_heap inventory healthy.
  unknown                         /sys/class/dma_heap absent
                                   (kernel without CONFIG_DMABUF
                                   _HEAPS).

stdlib only.
"""
from __future__ import annotations

import os
import stat
from typing import List, Optional


NAME = "dma_heap_audit"


_SYS_DMA_HEAP = "/sys/class/dma_heap"
_DEV_DMA_HEAP = "/dev/dma_heap"
_SYS_DRM = "/sys/class/drm"


_CMA_NAMES = {"cma", "linux,cma", "reserved", "dma-pool"}


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def list_heaps(sys_dma_heap: str = _SYS_DMA_HEAP,
                dev_dma_heap: str = _DEV_DMA_HEAP
                ) -> List[dict]:
    if not os.path.isdir(sys_dma_heap):
        return []
    try:
        names = sorted(os.listdir(sys_dma_heap))
    except OSError:
        return []
    out: List[dict] = []
    for n in names:
        d = os.path.join(sys_dma_heap, n)
        if not os.path.isdir(d) and not os.path.islink(d):
            continue
        entry = {"name": n,
                    "dev_node_present": False,
                    "dev_node_mode": None}
        node = os.path.join(dev_dma_heap, n)
        try:
            st = os.stat(node)
            entry["dev_node_present"] = True
            entry["dev_node_mode"] = stat.S_IMODE(st.st_mode)
        except OSError:
            pass
        out.append(entry)
    return out


def detect_gpu_presence(sys_drm: str = _SYS_DRM) -> bool:
    """Returns True if at least one DRM card device exists
    (NVIDIA / virtio_gpu / amdgpu / i915 / nouveau / xe / etc.)."""
    if not os.path.isdir(sys_drm):
        return False
    try:
        names = os.listdir(sys_drm)
    except OSError:
        return False
    return any(n.startswith("card") and n[4:].isdigit()
                   for n in names)


def classify(present: bool, heaps: List[dict],
              gpu_present: bool) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": ("/sys/class/dma_heap absent — "
                          "kernel built without "
                          "CONFIG_DMABUF_HEAPS."),
                "recommendation": ""}

    # 1) heaps_world_writable
    ww = [h for h in heaps
            if h.get("dev_node_mode") is not None
              and (h["dev_node_mode"] & 0o002)]
    if ww:
        sample = ", ".join(
            f"{h['name']} mode=0o{h['dev_node_mode']:03o}"
                for h in ww[:3])
        return {"verdict": "heaps_world_writable",
                "reason": (f"{len(ww)} dma-buf heap dev node(s) "
                          f"world-writable : {sample}."),
                "recommendation": _recipe_ww()}

    has_cma = any(h["name"] in _CMA_NAMES for h in heaps)
    only_system = (len(heaps) == 1
                        and heaps[0]["name"] == "system")

    # 2) cma_heap_missing_for_dma_buf — GPU present, no CMA
    if gpu_present and not has_cma and heaps:
        return {"verdict": "cma_heap_missing_for_dma_buf",
                "reason": (f"DRM GPU present but no CMA dma-buf "
                          f"heap registered (heaps : "
                          f"{', '.join(h['name'] for h in heaps)}). "
                          f"Zero-copy paths may fall back to "
                          f"CPU copies."),
                "recommendation": _recipe_cma_missing()}

    # 3) only_system_heap_present — informational
    if only_system and not gpu_present:
        return {"verdict": "only_system_heap_present",
                "reason": ("Only the 'system' dma_heap is "
                          "registered, no GPU detected — "
                          "informational."),
                "recommendation": _recipe_only_system()}

    # 4) heap_perms_root_only — all dev nodes are 0600
    all_root_only = (heaps
                            and all(h.get("dev_node_mode") == 0o600
                                       for h in heaps))
    if all_root_only:
        return {"verdict": "heap_perms_root_only",
                "reason": (f"All {len(heaps)} dma-buf heap dev "
                          f"node(s) are 0600 root-only. "
                          f"Unprivileged compositors / VAAPI "
                          f"clients fall back to slow paths."),
                "recommendation": _recipe_perms()}

    return {"verdict": "ok",
            "reason": (f"{len(heaps)} dma-buf heap(s) ; "
                      f"cma={has_cma} ; gpu={gpu_present}."),
            "recommendation": ""}


def status(config=None,
            sys_dma_heap: str = _SYS_DMA_HEAP,
            dev_dma_heap: str = _DEV_DMA_HEAP,
            sys_drm: str = _SYS_DRM) -> dict:
    present = os.path.isdir(sys_dma_heap)
    heaps = list_heaps(sys_dma_heap, dev_dma_heap)
    gpu_present = detect_gpu_presence(sys_drm)
    verdict = classify(present, heaps, gpu_present)
    return {"ok": present,
              "present": present,
              "heap_count": len(heaps),
              "heaps": heaps,
              "gpu_present": gpu_present,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_ww() -> str:
    return ("# World-writable dma-buf heap dev nodes = privilege\n"
            "# escalation surface. Restrict via udev :\n"
            "echo 'KERNEL==\"dma_heap\", MODE=\"0660\", "
            "GROUP=\"video\"' \\\n"
            "  | sudo tee /etc/udev/rules.d/99-dma-heap.rules\n"
            "sudo udevadm trigger\n")


def _recipe_cma_missing() -> str:
    return ("# CMA dma-buf heap absent. Modprobe the helper :\n"
            "sudo modprobe dma_heap_cma\n"
            "# Confirm registration :\n"
            "ls /sys/class/dma_heap/\n"
            "# Persist via /etc/modules-load.d/ if it sticks.\n")


def _recipe_only_system() -> str:
    return ("# Only the 'system' heap present. For GPU/V4L2\n"
            "# zero-copy : add CMA or system-uncached heaps :\n"
            "sudo modprobe dma_heap_cma\n"
            "# Reboot may be required if no CMA region reserved\n"
            "# at boot via 'cma=' kernel cmdline.\n")


def _recipe_perms() -> str:
    return ("# dma_heap nodes 0600 root-only. Add a udev rule to\n"
            "# expose to the 'video' group (or 'render' on some\n"
            "# distros) :\n"
            "echo 'KERNEL==\"dma_heap*\", MODE=\"0660\", "
            "GROUP=\"video\"' \\\n"
            "  | sudo tee /etc/udev/rules.d/99-dma-heap.rules\n"
            "sudo udevadm trigger\n")

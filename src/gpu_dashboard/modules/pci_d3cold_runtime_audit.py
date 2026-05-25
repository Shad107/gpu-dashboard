"""Module pci_d3cold_runtime_audit — GPU runtime PM posture
audit walking the PCI hierarchy (R&D #97.4).

Existing modules (pcie_aspm_audit, pcie_l1ss_audit,
pcie_width_watcher) inspect ASPM / link state but none of
them look at runtime-PM D3cold reachability, which is what
actually drops the slot to ~0W during desktop idle.

D3cold cascade: a PCIe endpoint can only enter D3cold if
EVERY upstream bridge / root-port up to the host bridge
also has d3cold_allowed=1 + power/control=auto.

If ANY upstream link is pinned (d3cold_allowed=0 or
power/control=on), the GPU silently stays in D3hot and
idle desktop wastes ~10-30 W on a discrete RTX 3090.

Reads :

  /sys/class/drm/card<N>/device         (GPU PCI device)
  /sys/bus/pci/devices/<addr>/{
      d3cold_allowed,
      power/control,
      power/runtime_status,
      power/autosuspend_delay_ms,
      power/runtime_suspended_time,
      power/runtime_active_time,
  }

Verdicts (worst-first) :

  gpu_d3cold_blocked_by_upstream  err    GPU d3cold_allowed=1
                                         but an upstream
                                         bridge has
                                         d3cold_allowed=0
                                         or control=on —
                                         the chain blocks
                                         D3cold cascade.
  runtime_pm_disabled_on_gpu      warn   GPU power/control=on
                                         (manually pinned),
                                         never auto-suspends.
  autosuspend_delay_unset         accent autosuspend_delay_ms
                                         missing while
                                         control=auto.
  suspended_active_ratio_low      accent GPU is on auto but
                                         runtime_suspended_time
                                         vs runtime_active_time
                                         ratio < 5 %.
  ok                                     d3cold-capable chain.
  requires_root                          power/* unreadable.
  unknown                                no GPU PCI device found.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "pci_d3cold_runtime_audit"

DEFAULT_DRM_ROOT = "/sys/class/drm"
DEFAULT_PCI_DEVS = "/sys/bus/pci/devices"

# Ignore non-NVIDIA virtualized GPUs (cirrus, vmware, virtio)
_NVIDIA_VENDOR = "0x10de"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def _read_str(path: str) -> Optional[str]:
    t = _read_text(path)
    return t.strip() if t else None


def find_gpu_pci_path(drm_root: str = DEFAULT_DRM_ROOT
                       ) -> Optional[str]:
    """Locate the GPU's PCI device dir.

    Walks /sys/class/drm/card<N>/device and picks the first
    NVIDIA discrete card (vendor 0x10de). Returns the absolute
    /sys/devices/... path, or None.
    """
    if not os.path.isdir(drm_root):
        return None
    try:
        entries = sorted(os.listdir(drm_root))
    except OSError:
        return None
    for ent in entries:
        if not (ent.startswith("card")
                and ent[4:].isdigit()):
            continue
        dev_link = os.path.join(drm_root, ent, "device")
        if not os.path.islink(dev_link):
            continue
        try:
            real = os.path.realpath(dev_link)
        except OSError:
            continue
        vendor = _read_str(os.path.join(real, "vendor"))
        if vendor == _NVIDIA_VENDOR:
            return real
    # Fallback: first card with a "device" symlink at all
    for ent in entries:
        if not (ent.startswith("card")
                and ent[4:].isdigit()):
            continue
        dev_link = os.path.join(drm_root, ent, "device")
        if os.path.islink(dev_link):
            try:
                return os.path.realpath(dev_link)
            except OSError:
                continue
    return None


def upstream_chain(gpu_path: str) -> list:
    """Walk pci0000:* parents up to (but not including) the
    host bridge directory. Each entry is the absolute path."""
    out: list = []
    cur = os.path.dirname(gpu_path)
    while cur and os.path.basename(cur).startswith("0000:"):
        out.append(cur)
        cur = os.path.dirname(cur)
    return out


def _read_device(path: str) -> dict:
    """Read a PCI device's d3cold + power knobs."""
    power = os.path.join(path, "power")
    return {
        "path": path,
        "addr": os.path.basename(path),
        "d3cold_allowed": _read_int(
            os.path.join(path, "d3cold_allowed")),
        "control": _read_str(
            os.path.join(power, "control")),
        "runtime_status": _read_str(
            os.path.join(power, "runtime_status")),
        "autosuspend_delay_ms": _read_int(
            os.path.join(power, "autosuspend_delay_ms")),
        "runtime_suspended_time": _read_int(
            os.path.join(power, "runtime_suspended_time")),
        "runtime_active_time": _read_int(
            os.path.join(power, "runtime_active_time")),
    }


def classify(gpu: Optional[dict],
             upstream: list,
             gpu_unreadable: bool) -> dict:
    if gpu is None:
        return {"verdict": "unknown",
                "reason": (
                    "No GPU PCI device found under "
                    "/sys/class/drm — virtualised / "
                    "headless host.")}
    if gpu_unreadable:
        return {"verdict": "requires_root",
                "reason": (
                    "GPU power/ knobs unreadable — re-run "
                    "as root.")}

    # err — any upstream bridge blocks the D3cold cascade
    blockers = []
    for u in upstream:
        if u["d3cold_allowed"] == 0:
            blockers.append((u["addr"],
                             "d3cold_allowed=0"))
        elif u["control"] == "on":
            blockers.append((u["addr"], "control=on"))
    if blockers and gpu.get("d3cold_allowed") == 1:
        return {
            "verdict": "gpu_d3cold_blocked_by_upstream",
            "reason": (
                "GPU has d3cold_allowed=1 but the upstream "
                f"PCIe chain blocks D3cold cascade: "
                f"{blockers}. The slot stays in D3hot at "
                "idle and wastes ~10-30 W.")}

    # warn — GPU runtime-PM pinned on
    if gpu.get("control") == "on":
        return {
            "verdict": "runtime_pm_disabled_on_gpu",
            "reason": (
                "GPU power/control=on — runtime PM disabled, "
                "GPU never auto-suspends. Often set by the "
                "NVIDIA driver when persistence-mode is on.")}

    # accent — autosuspend_delay_ms unset
    if gpu.get("autosuspend_delay_ms") is None:
        return {
            "verdict": "autosuspend_delay_unset",
            "reason": (
                "power/autosuspend_delay_ms unreadable "
                "despite control=auto — runtime PM may not "
                "be wired through by the driver.")}

    # accent — suspended/active ratio low
    susp = gpu.get("runtime_suspended_time") or 0
    act = gpu.get("runtime_active_time") or 0
    total = susp + act
    ratio = (susp / total) if total else 0.0
    if total > 60_000 and ratio < 0.05:
        return {
            "verdict": "suspended_active_ratio_low",
            "reason": (
                f"GPU on auto but only {ratio:.1%} of time "
                f"spent suspended ({susp} ms vs {act} ms "
                "active). Something is keeping it awake — "
                "compositor, nvidia-persistenced, polling app.")}

    return {"verdict": "ok",
            "reason": (
                f"GPU {gpu['addr']} on auto runtime PM ; "
                f"{len(upstream)} upstream bridge(s) allow "
                "D3cold cascade.")}


def status(config: Optional[dict] = None,
           drm_root: str = DEFAULT_DRM_ROOT) -> dict:
    gpu_path = find_gpu_pci_path(drm_root)
    if gpu_path is None:
        verdict = classify(None, [], False)
        return {
            "ok": False,
            "gpu_addr": None,
            "upstream_count": 0,
            "verdict": verdict,
        }

    gpu = _read_device(gpu_path)
    # Treat as unreadable if neither control nor d3cold_allowed
    # could be read — the device dir is permission-protected.
    gpu_unreadable = (
        gpu["control"] is None
        and gpu["d3cold_allowed"] is None)
    upstream_paths = upstream_chain(gpu_path)
    upstream = [_read_device(p) for p in upstream_paths]
    verdict = classify(
        None if gpu_unreadable and gpu_path is None else gpu,
        upstream, gpu_unreadable)
    return {
        "ok": verdict["verdict"] == "ok",
        "gpu_addr": gpu["addr"],
        "gpu_d3cold_allowed": gpu["d3cold_allowed"],
        "gpu_control": gpu["control"],
        "gpu_runtime_status": gpu["runtime_status"],
        "upstream_count": len(upstream),
        "upstream": [
            {"addr": u["addr"],
             "d3cold_allowed": u["d3cold_allowed"],
             "control": u["control"]} for u in upstream],
        "verdict": verdict,
    }

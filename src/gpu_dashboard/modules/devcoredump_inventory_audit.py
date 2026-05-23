"""Module devcoredump_inventory_audit — devcoredump inventory
(R&D #70.4).

The Linux devcoredump framework saves driver-specific binary
crash dumps when a hardware-managed device hits an unrecoverable
fault. Each pending dump appears as
`/sys/class/devcoredump/devcd<N>/` for a configurable window
(default 5 minutes) and then auto-evaporates. Crucially :

  * GPU drivers (i915, amdgpu, msm, panfrost, nouveau, the
    NVIDIA open driver) write GPU hang state here.
  * NVMe / wifi / DRM drivers do likewise.

A pending devcoredump that nobody collects = lost diagnostic
data. The audit surfaces them while still readable.

Reads :
  /sys/class/devcoredump/disabled                 0/1 toggle
  /sys/class/devcoredump/devcd<N>/{data,
                                     failing_device,
                                     disabled}
  /sys/module/<drv>/parameters/disable_devcoredump (per-driver
                                                     opt-out)

Verdicts (priority order) :
  gpu_devcoredump_present              ≥1 pending devcd* AND
                                         its failing_device path
                                         matches a GPU (drm/...
                                         or nvidia / amdgpu /
                                         i915 / nouveau / msm /
                                         panfrost).
  recent_devcoredump_pending           ≥1 pending devcd* (any
                                         driver).
  devcoredump_disabled_globally        /sys/class/devcoredump/
                                         disabled = 1.
  devcoredump_capability_missing       /sys/class/devcoredump
                                         absent (CONFIG_WANT_DEV_
                                         COREDUMP=n).
  ok                                   framework live, no pending
                                         dumps.
  unknown                              probe failed unexpectedly.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "devcoredump_inventory_audit"


_SYS_DEVCOREDUMP = "/sys/class/devcoredump"
_SYS_MODULE = "/sys/module"

_GPU_DRIVER_RE = re.compile(
    r"(?:nvidia|amdgpu|radeon|i915|xe|nouveau|msm|"
    r"panfrost|lima|v3d|virtio-gpu)",
    re.IGNORECASE)


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def read_global_disabled(sys_path: str = _SYS_DEVCOREDUMP
                              ) -> Optional[int]:
    return _read_int(os.path.join(sys_path, "disabled"))


def list_pending_dumps(sys_path: str = _SYS_DEVCOREDUMP
                            ) -> List[dict]:
    """Returns one entry per /sys/class/devcoredump/devcd<N>/."""
    if not os.path.isdir(sys_path):
        return []
    try:
        names = sorted(os.listdir(sys_path))
    except OSError:
        return []
    out: List[dict] = []
    for n in names:
        if not n.startswith("devcd"):
            continue
        d = os.path.join(sys_path, n)
        if not os.path.isdir(d):
            continue
        failing = _read(os.path.join(d, "failing_device"))
        # 'data' may be huge ; just check presence + size.
        data_path = os.path.join(d, "data")
        data_present = False
        data_size = None
        try:
            st = os.stat(data_path)
            data_present = True
            data_size = st.st_size
        except OSError:
            pass
        out.append({
            "id": n,
            "failing_device": failing,
            "data_present": data_present,
            "data_size": data_size,
            "disabled": _read_int(os.path.join(d, "disabled")),
            "is_gpu": bool(failing
                              and _GPU_DRIVER_RE.search(failing)),
        })
    return out


def per_driver_opt_outs(sys_module: str = _SYS_MODULE
                              ) -> List[dict]:
    if not os.path.isdir(sys_module):
        return []
    out: List[dict] = []
    try:
        names = os.listdir(sys_module)
    except OSError:
        return []
    for n in names:
        p = os.path.join(sys_module, n, "parameters",
                              "disable_devcoredump")
        if os.path.isfile(p):
            v = _read(p)
            out.append({"module": n, "disabled": v})
    return out


def classify(dumps: List[dict],
              global_disabled: Optional[int],
              capability_present: bool) -> dict:
    if not capability_present:
        return {"verdict": "devcoredump_capability_missing",
                "reason": ("/sys/class/devcoredump absent — "
                          "kernel built without "
                          "CONFIG_WANT_DEV_COREDUMP. Driver "
                          "crash data won't be captured."),
                "recommendation": _recipe_capability_missing()}

    # 1) gpu_devcoredump_present
    gpu_dumps = [d for d in dumps if d.get("is_gpu")]
    if gpu_dumps:
        sample = ", ".join(
            f"{d['id']} fail={d.get('failing_device')}"
                for d in gpu_dumps[:3])
        return {"verdict": "gpu_devcoredump_present",
                "reason": (f"{len(gpu_dumps)} GPU devcoredump "
                          f"pending : {sample}. Collect now "
                          f"before auto-evaporation."),
                "recommendation": _recipe_gpu_devcd()}

    # 2) recent_devcoredump_pending — non-GPU
    if dumps:
        sample = ", ".join(d["id"] for d in dumps[:3])
        return {"verdict": "recent_devcoredump_pending",
                "reason": (f"{len(dumps)} non-GPU devcoredump "
                          f"pending : {sample}."),
                "recommendation": _recipe_pending_devcd()}

    # 3) devcoredump_disabled_globally
    if global_disabled == 1:
        return {"verdict": "devcoredump_disabled_globally",
                "reason": ("/sys/class/devcoredump/disabled = 1 — "
                          "driver crash dumps will not be "
                          "captured."),
                "recommendation": _recipe_global_disabled()}

    return {"verdict": "ok",
            "reason": (f"Framework live ; disabled={global_disabled}"
                      f" ; no pending dumps."),
            "recommendation": ""}


def status(config=None,
            sys_devcoredump: str = _SYS_DEVCOREDUMP,
            sys_module: str = _SYS_MODULE) -> dict:
    capability_present = os.path.isdir(sys_devcoredump)
    global_disabled = read_global_disabled(sys_devcoredump)
    dumps = list_pending_dumps(sys_devcoredump)
    opt_outs = per_driver_opt_outs(sys_module)
    verdict = classify(dumps, global_disabled,
                          capability_present)
    return {"ok": capability_present,
              "capability_present": capability_present,
              "global_disabled": global_disabled,
              "pending_count": len(dumps),
              "pending_dumps": dumps,
              "per_driver_opt_outs": opt_outs,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_capability_missing() -> str:
    return ("# Kernel built without CONFIG_WANT_DEV_COREDUMP.\n"
            "# Devices that crash will not produce diagnostic\n"
            "# dumps. Rebuild kernel with :\n"
            "#   CONFIG_DEV_COREDUMP=y\n"
            "# Or install a distro kernel that ships it.\n")


def _recipe_gpu_devcd() -> str:
    return ("# A GPU devcoredump is pending. Collect quickly :\n"
            "for d in /sys/class/devcoredump/devcd*; do\n"
            "  echo \"-- $d\"\n"
            "  sudo cat \"$d/failing_device\"\n"
            "  sudo cp \"$d/data\" \\\n"
            "    /var/log/gpu-coredump-$(basename $d).bin\n"
            "done\n"
            "# Then mark each one as collected (frees slot) :\n"
            "echo 1 | sudo tee \"$d/disabled\"\n")


def _recipe_pending_devcd() -> str:
    return ("# A non-GPU devcoredump is pending. Inspect :\n"
            "for d in /sys/class/devcoredump/devcd*; do\n"
            "  sudo cat \"$d/failing_device\"\n"
            "done\n"
            "# Pendings auto-expire ~5 min ; collect via :\n"
            "sudo cp /sys/class/devcoredump/<id>/data /tmp/\n")


def _recipe_global_disabled() -> str:
    return ("# Re-enable devcoredump globally :\n"
            "echo 0 | sudo tee /sys/class/devcoredump/disabled\n"
            "# Confirm :\n"
            "cat /sys/class/devcoredump/disabled\n")

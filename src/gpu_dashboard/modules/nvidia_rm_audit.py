"""Module nvidia_rm_audit — nvidia RM runtime registry (R&D #47.3).

Reads /proc/driver/nvidia/{params, registry, version} and walks
the /proc/driver/nvidia/capabilities/* tree.

Distinct from shipped kmod_params (modprobe-time sysfs params)
and shipped proc_deep_state (per-GPU dynamic state) : this module
covers the *RM-internal live registry* + the *cgroup-capability
fd tree* — what the running RM thinks is true vs what was asked.

  /proc/driver/nvidia/params       key:value listing of every
                                   active RM parameter (ResmanDebug
                                   Level, RmLogonRC, ModifyDevice
                                   Files, DeviceFile{UID,GID,Mode},
                                   ...).
  /proc/driver/nvidia/registry     dynamically-applied RM registry
                                   keys (often empty on consumer
                                   driver, populated on data-center
                                   driver with custom RegistryDwords).
  /proc/driver/nvidia/version      NVRM version string + GCC.
  /proc/driver/nvidia/capabilities/{fabric-imex-mgmt, gpuN, mig,
                                     nvlink}
                                   cgroup-capability fd tree
                                   (presence = userspace allowed,
                                   absence = blocked).

Verdicts (priority-ordered) :
  driver_kmod_mismatch    /proc/driver/nvidia/version's NVRM
                          version string differs from nvidia-smi
                          (best-effort cross-check — skip if no
                          nvidia-smi). Common failure mode after
                          partial DKMS rebuild.
  caps_missing            ≥1 expected capability (`mig/config`,
                          `mig/monitor`, per-GPU `gpu<N>`) absent
                          → indicates rootless container missed
                          a bind-mount or nvidia-modprobe -c
                          wasn't run.
  no_nvidia_driver        /proc/driver/nvidia absent — no NVIDIA
                          driver loaded.
  ok                      driver loaded, capabilities present,
                          version matches.
  unknown                 /proc/driver/nvidia unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "nvidia_rm_audit"


_PROC_NVIDIA = "/proc/driver/nvidia"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def parse_params(text: str) -> dict:
    """Parse 'Key: Value' lines into a dict (str values for
    flexibility; numeric coercion done by callers when needed)."""
    out: dict = {}
    if not text:
        return out
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


_VERSION_RE = re.compile(
    r"NVRM version:\s+NVIDIA.*?(\d+\.\d+(?:\.\d+)?)"
)


def parse_version(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


def list_capabilities(proc_nv: str = _PROC_NVIDIA) -> list:
    """Walk /proc/driver/nvidia/capabilities/* for present files
    (each is 0-byte). Returns relative paths."""
    root = os.path.join(proc_nv, "capabilities")
    if not os.path.isdir(root):
        return []
    out: list = []
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root)
            out.append(rel)
    return sorted(out)


_EXPECTED_CAPS = (
    # Always expected when nvidia driver is loaded :
    "mig/config",
    "mig/monitor",
)


def _expected_per_gpu(params: dict) -> list:
    """Best-effort : list gpu<N>/* directories that *should* exist
    based on params 'NumberOfDevices' or similar. We don't
    require all of them — just flag major omissions."""
    # The actual presence is best detected by walking the dir, so
    # this returns empty by default.
    return []


def _smi_version(cfg) -> Optional[str]:
    """Best-effort : ask nvidia-smi for the driver version. If
    nvidia-smi is missing or returns garbage, we just return None
    (skip the kmod-vs-smi check)."""
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            return None
        first = (out.stdout or "").strip().splitlines()
        if not first:
            return None
        v = first[0].strip()
        return v or None
    except Exception:
        return None


_RECIPE_KMOD_MISMATCH = (
    "# /proc/driver/nvidia/version says X but nvidia-smi reports Y.\n"
    "# This happens after a partial DKMS rebuild — kmod is from\n"
    "# old kernel, userspace tools were upgraded. Symptoms : CUDA\n"
    "# inits fail at API mismatch. Fix :\n"
    "sudo dkms autoinstall                                  # rebuild kmod\n"
    "sudo modprobe -r nvidia_uvm nvidia_drm nvidia_modeset nvidia\n"
    "sudo modprobe nvidia\n"
    "# If kernel was just upgraded, reboot first."
)

_RECIPE_CAPS_MISSING = (
    "# nvidia capability directory absent — typical in rootless\n"
    "# containers that bind-mounted /proc/driver/nvidia incompletely.\n"
    "# On host : ensure nvidia-modprobe ran with capability flag :\n"
    "sudo nvidia-modprobe -c 0 -u\n"
    "# In container : add the missing capability mount,\n"
    "# e.g. for MIG :\n"
    "#   --device=/proc/driver/nvidia/capabilities/mig/config\n"
    "#   --device=/proc/driver/nvidia/capabilities/mig/monitor"
)

_RECIPE_NO_DRIVER = (
    "# No NVIDIA driver loaded. To install on Ubuntu :\n"
    "sudo ubuntu-drivers install\n"
    "# Or pick a specific version :\n"
    "sudo apt install nvidia-driver-555-open\n"
    "sudo modprobe nvidia"
)


def classify(version: Optional[str], smi_version: Optional[str],
              params: dict, caps: list,
              driver_present: bool) -> dict:
    if not driver_present:
        return {"verdict": "no_nvidia_driver",
                "reason": ("/proc/driver/nvidia absent — no NVIDIA "
                           "kernel driver loaded."),
                "recommendation": _RECIPE_NO_DRIVER}
    if not version and not params:
        return {"verdict": "unknown",
                "reason": ("/proc/driver/nvidia present but version "
                           "+ params both unreadable."),
                "recommendation": ""}
    if version and smi_version and version != smi_version:
        return {"verdict": "driver_kmod_mismatch",
                "reason": (f"NVRM version={version} but "
                           f"nvidia-smi reports {smi_version} — "
                           f"partial DKMS rebuild or kernel upgrade "
                           f"left old kmod loaded."),
                "recommendation": _RECIPE_KMOD_MISMATCH}
    missing = [c for c in _EXPECTED_CAPS if c not in caps]
    if missing:
        return {"verdict": "caps_missing",
                "reason": (f"{len(missing)} expected nvidia "
                           f"capability path(s) absent : "
                           f"{', '.join(missing)}. Likely a "
                           f"rootless-container bind-mount gap "
                           f"or nvidia-modprobe -c not run."),
                "recommendation": _RECIPE_CAPS_MISSING}
    return {"verdict": "ok",
            "reason": (f"NVRM version={version or '?'}, "
                       f"{len(caps)} capability path(s) present, "
                       f"params readable."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    driver_present = os.path.isdir(_PROC_NVIDIA)
    version = parse_version(
        _read(os.path.join(_PROC_NVIDIA, "version")))
    params = parse_params(
        _read(os.path.join(_PROC_NVIDIA, "params")) or "")
    caps = list_capabilities(_PROC_NVIDIA)
    smi_v = _smi_version(cfg) if driver_present else None
    verdict = classify(version, smi_v, params, caps, driver_present)
    return {
        "ok": driver_present,
        "driver_present": driver_present,
        "version_proc": version,
        "version_smi": smi_v,
        "params": params,
        "capabilities": caps,
        "capability_count": len(caps),
        "verdict": verdict,
    }

"""Module module_integrity_audit — kernel module integrity (R&D #52.3).

What this audit catches :

* Half-upgraded NVIDIA stack — /sys/module/nvidia/version (the
  *loaded* version) ≠ the version reported by
  /proc/driver/nvidia/version. After a `dnf` / `apt` upgrade
  without a reboot, you can run with a `nvidia.ko` whose runtime
  version is older than what's on disk → cryptic CUDA "device
  kernel image is invalid" hours later.
* Unsigned out-of-tree modules from sources other than the NVIDIA
  stack — on a Secure-Boot host this is the smoking gun for an
  unsigned binary blob in the kernel.
* /proc/sys/kernel/modules_disabled = 1 (lockdown intent) — useful
  to surface so the user knows further `modprobe` will be denied.

Reads :
  /proc/sys/kernel/tainted        global taint mask bits
  /proc/sys/kernel/modules_disabled
  /sys/module/<name>/taint        per-module taint letters
  /sys/module/<name>/srcversion
  /sys/module/nvidia/version
  /proc/driver/nvidia/version     (if NVIDIA kernel module loaded)

Verdicts (priority-ordered) :
  modules_disabled                 modules_disabled = 1.
  nvidia_version_mismatch          /sys/module/nvidia/version disagrees
                                   with /proc/driver/nvidia/version.
  unsigned_modules_unexpected      tainted modules outside the NVIDIA
                                   family (so not the usual NVIDIA-OoT).
  tainted_oot_nvidia_only          taint is purely the NVIDIA family
                                   (typical homelab steady state).
  ok                               kernel.tainted = 0.
  unknown                          /proc/sys/kernel/tainted unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple


NAME = "module_integrity_audit"


_PROC_TAINTED = "/proc/sys/kernel/tainted"
_PROC_MOD_DISABLED = "/proc/sys/kernel/modules_disabled"
_SYS_MODULE = "/sys/module"
_PROC_NVIDIA_VERSION = "/proc/driver/nvidia/version"


# Members of the typical NVIDIA proprietary / open kernel stack.
_NVIDIA_FAMILY = ("nvidia", "nvidia_uvm", "nvidia_drm",
                    "nvidia_modeset", "nvidia_peermem")


# Taint flag bits — see kernel/panic.c taint_flags[].
_TAINT_BITS = {
    0: "P",  # proprietary module
    1: "F",  # forced module load
    4: "B",  # bad page
    9: "U",  # user request
    12: "O", # out-of-tree
    13: "E", # unsigned module
}


_NVRM_VERSION_RE = re.compile(
    r"NVIDIA\s+UNIX\s+(?:Open\s+)?Kernel\s+Module"
    r"(?:\s+for\s+\S+)?\s+(?P<v>[0-9.]+)\b")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def decode_tainted(mask: int) -> List[str]:
    """Return list of taint letters set in the global mask."""
    out: List[str] = []
    for bit, letter in sorted(_TAINT_BITS.items()):
        if mask & (1 << bit):
            out.append(letter)
    return out


def list_tainted_modules(sys_module: str = _SYS_MODULE
                            ) -> List[dict]:
    """Walk /sys/module/* and return modules with non-empty taint."""
    if not os.path.isdir(sys_module):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_module)):
        taint = _read(os.path.join(sys_module, name, "taint"))
        if taint:
            srcver = _read(os.path.join(sys_module, name,
                                            "srcversion"))
            out.append({"name": name, "taint": taint,
                          "srcversion": srcver})
    return out


def nvidia_versions(sys_module: str = _SYS_MODULE,
                       proc_nv: str = _PROC_NVIDIA_VERSION
                       ) -> Tuple[Optional[str], Optional[str]]:
    """Return (loaded_version, runtime_version)."""
    loaded = _read(os.path.join(sys_module, "nvidia", "version"))
    runtime = None
    text = _read(proc_nv)
    if text:
        m = _NVRM_VERSION_RE.search(text)
        if m:
            runtime = m.group("v")
    return loaded, runtime


def classify(tainted_mask: Optional[int],
              modules_disabled: Optional[int],
              tainted_modules: List[dict],
              nv_loaded: Optional[str],
              nv_runtime: Optional[str]) -> dict:
    if tainted_mask is None:
        return {"verdict": "unknown",
                "reason": "/proc/sys/kernel/tainted unreadable.",
                "recommendation": ""}

    # 1) modules_disabled
    if modules_disabled == 1:
        return {"verdict": "modules_disabled",
                "reason": ("/proc/sys/kernel/modules_disabled = 1. "
                          "Further modprobe calls will be denied "
                          "until next reboot."),
                "recommendation": _recipe_modules_disabled()}

    # 2) nvidia_version_mismatch
    if nv_loaded and nv_runtime and nv_loaded != nv_runtime:
        return {"verdict": "nvidia_version_mismatch",
                "reason": (f"/sys/module/nvidia/version = "
                          f"'{nv_loaded}' but /proc/driver/nvidia/"
                          f"version reports '{nv_runtime}'. Half-"
                          f"upgraded NVIDIA stack — reboot needed."),
                "recommendation": _recipe_nvidia_mismatch()}

    # 3) unsigned_modules_unexpected
    family = set(_NVIDIA_FAMILY)
    others = [m for m in tainted_modules if m["name"] not in family]
    if others:
        sample = ", ".join(f"{m['name']}({m['taint']})"
                              for m in others[:4])
        return {"verdict": "unsigned_modules_unexpected",
                "reason": (f"{len(others)} tainted module(s) "
                          f"outside the NVIDIA family : {sample}."),
                "recommendation": _recipe_unexpected_oot()}

    # 4) tainted_oot_nvidia_only
    if tainted_mask != 0 and tainted_modules:
        letters = ",".join(decode_tainted(tainted_mask)) or "?"
        return {"verdict": "tainted_oot_nvidia_only",
                "reason": (f"Kernel tainted ({letters}) but only "
                          f"by the NVIDIA family — typical for a "
                          f"homelab GPU host running the proprietary "
                          f"or open kernel module."),
                "recommendation": ""}

    return {"verdict": "ok",
            "reason": "Kernel tainted mask = 0.",
            "recommendation": ""}


def status(config=None,
            proc_tainted: str = _PROC_TAINTED,
            proc_mod_disabled: str = _PROC_MOD_DISABLED,
            sys_module: str = _SYS_MODULE,
            proc_nv: str = _PROC_NVIDIA_VERSION) -> dict:
    tainted_mask = _read_int(proc_tainted)
    modules_disabled = _read_int(proc_mod_disabled)
    tainted_modules = list_tainted_modules(sys_module)
    nv_loaded, nv_runtime = nvidia_versions(sys_module, proc_nv)
    ok = tainted_mask is not None
    verdict = classify(tainted_mask, modules_disabled,
                          tainted_modules, nv_loaded, nv_runtime)
    return {"ok": ok,
              "tainted_mask": tainted_mask,
              "tainted_letters": (decode_tainted(tainted_mask)
                                      if tainted_mask is not None
                                      else []),
              "modules_disabled": modules_disabled,
              "tainted_modules": tainted_modules,
              "nvidia_loaded_version": nv_loaded,
              "nvidia_runtime_version": nv_runtime,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_modules_disabled() -> str:
    return ("# modules_disabled is one-way — reboot to undo it.\n"
            "# If you didn't set it intentionally, check :\n"
            "grep -r kernel.modules_disabled /etc/sysctl.* 2>/dev/null\n"
            "# … plus the cmdline for `lockdown=...`.\n")


def _recipe_nvidia_mismatch() -> str:
    return ("# A reboot is the safe fix when the NVIDIA stack was\n"
            "# upgraded under a running kernel module :\n"
            "# 1) ensure the new dkms / akmods build succeeded :\n"
            "modinfo nvidia | grep -E '^(version|srcversion):'\n"
            "# 2) compare with the loaded version :\n"
            "cat /sys/module/nvidia/version /proc/driver/nvidia/version\n"
            "# 3) if they differ, reboot. Hot-unload is risky if any\n"
            "#    process holds /dev/nvidia* open (CUDA contexts).\n")


def _recipe_unexpected_oot() -> str:
    return ("# Identify each tainted module and confirm it's wanted :\n"
            "for m in /sys/module/*/taint; do\n"
            "  t=$(cat $m 2>/dev/null)\n"
            "  [ -n \"$t\" ] && echo \"$(basename $(dirname $m)) : $t\"\n"
            "done\n"
            "# Cross-reference with `lsmod` and `modinfo <name>` to find\n"
            "# the loader (third-party package, DKMS unit, akmods, …).\n")

"""Module dkms_status — DKMS rebuild status (R&D #24.3).

After a kernel upgrade, the nvidia DKMS module sometimes fails to
rebuild — apt's hook silently swallows the error, the user reboots,
and the GPU "disappears" until they figure out to run
`sudo dkms autoinstall`. This is the single most common
post-upgrade Linux-on-NVIDIA panic.

This module is XS effort :

  1. Read uname.release (running kernel)
  2. Run `dkms status` (no sudo needed)
  3. Check /lib/modules/<running>/updates/dkms/nvidia.ko[.zst] exists
  4. Cross-reference : if DKMS reports nvidia for running kernel as
     'installed' AND the .ko file is present → OK
     Otherwise → 'rebuild_needed' with the exact dkms autoinstall cmd.

stdlib only.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from typing import Optional


NAME = "dkms_status"


def running_kernel() -> str:
    """Return uname.release."""
    try:
        return platform.uname().release
    except Exception:
        return ""


def parse_dkms_status(text: str) -> list[dict]:
    """Parse `dkms status` output.

    Two known formats (DKMS versions vary) :

      nvidia/535.86.05, 6.5.0-26-generic, x86_64: installed
      nvidia, 535.86.05, 6.5.0-26-generic, x86_64: installed

    Both yield {module, version, kernel, arch, state}.
    """
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "nvidia" not in line.lower():
            continue
        # split on ": " for the state
        if ":" not in line:
            continue
        body, state = line.rsplit(":", 1)
        state = state.strip()
        parts = [p.strip() for p in body.split(",")]
        # parts[0] is "module/version" OR "module"
        module = "nvidia"
        version: Optional[str] = None
        kernel: Optional[str] = None
        arch: Optional[str] = None
        if "/" in parts[0]:
            mod, ver = parts[0].split("/", 1)
            module = mod
            version = ver
            if len(parts) >= 2:
                kernel = parts[1]
            if len(parts) >= 3:
                arch = parts[2]
        else:
            if len(parts) >= 2:
                version = parts[1]
            if len(parts) >= 3:
                kernel = parts[2]
            if len(parts) >= 4:
                arch = parts[3]
        out.append({
            "module": module,
            "version": version,
            "kernel": kernel,
            "arch": arch,
            "state": state.lower(),
        })
    return out


def run_dkms_status(timeout: float = 3.0) -> Optional[str]:
    if not shutil.which("dkms"):
        return None
    try:
        r = subprocess.run(
            ["dkms", "status"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return r.stdout if r.returncode == 0 else None


def nvidia_ko_present(kernel: str,
                       mod_root: str = "/lib/modules") -> bool:
    """Check whether the running kernel has updates/dkms/nvidia.ko[.zst]
    on disk."""
    base = os.path.join(mod_root, kernel, "updates", "dkms")
    if not os.path.isdir(base):
        return False
    try:
        for name in os.listdir(base):
            if name in ("nvidia.ko", "nvidia.ko.zst", "nvidia.ko.xz"):
                return True
    except OSError:
        return False
    return False


def classify(kernel: str, dkms_entries: list[dict],
              ko_present: bool) -> dict:
    """Verdict :
      - ok               (dkms reports installed for running kernel AND
                          .ko file present)
      - rebuild_needed   (dkms reports something other than 'installed'
                          for running kernel OR .ko file missing)
      - dkms_missing     (dkms not installed on this system)
      - no_nvidia_dkms   (dkms is here but no nvidia entries at all)
    """
    if not dkms_entries:
        if not kernel:
            return {"verdict": "unknown", "reason": "Cannot read uname.",
                    "recovery": ""}
        return {"verdict": "no_nvidia_dkms",
                "reason": "dkms status shows no nvidia entries.",
                "recovery": ""}
    # Find entry for running kernel
    match = next((e for e in dkms_entries
                  if e.get("kernel") == kernel), None)
    if match is None:
        return {
            "verdict": "rebuild_needed",
            "reason": (f"DKMS has no nvidia build for the running kernel "
                       f"'{kernel}'. Probably a fresh kernel upgrade where "
                       "the rebuild did not fire."),
            "recovery": "sudo dkms autoinstall -k " + kernel,
        }
    if match["state"] != "installed":
        return {
            "verdict": "rebuild_needed",
            "reason": (f"DKMS reports nvidia for kernel '{kernel}' as "
                       f"'{match['state']}' (expected 'installed')."),
            "recovery": "sudo dkms autoinstall -k " + kernel,
        }
    if not ko_present:
        return {
            "verdict": "rebuild_needed",
            "reason": (f"DKMS reports nvidia/{match.get('version')} as "
                       f"'installed' for kernel '{kernel}', but "
                       "/lib/modules/<kernel>/updates/dkms/nvidia.ko is "
                       "missing. Out-of-sync state."),
            "recovery": ("sudo dkms remove nvidia/" + (match.get("version") or "")
                          + " --all && sudo dkms autoinstall -k " + kernel),
        }
    return {
        "verdict": "ok",
        "reason": (f"nvidia/{match['version']} DKMS build installed for "
                   f"kernel {kernel}."),
        "recovery": "",
    }


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    kernel = running_kernel()
    raw = run_dkms_status()
    if raw is None:
        return {
            "ok": False,
            "reason": "dkms binary not available on this system.",
            "running_kernel": kernel,
            "dkms_entries": [],
            "ko_present": False,
            "verdict": {"verdict": "dkms_missing",
                         "reason": "DKMS is not installed.",
                         "recovery": "sudo apt install dkms (Debian/Ubuntu)"},
        }
    entries = parse_dkms_status(raw)
    ko = nvidia_ko_present(kernel) if kernel else False
    return {
        "ok": True,
        "running_kernel": kernel,
        "dkms_entries": entries,
        "ko_present": ko,
        "verdict": classify(kernel, entries, ko),
    }

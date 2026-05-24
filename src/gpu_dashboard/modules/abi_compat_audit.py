"""Module abi_compat_audit — kernel ABI compatibility audit
(R&D #74.3).

The kernel exposes ABI / personality knobs under
/proc/sys/abi/ and a few neighbours :

  /proc/sys/abi/vsyscall32       1 = enable vsyscall32 page
                                   needed by 32-bit glibc
                                   (Steam runtime, ancient CUDA
                                   samples, closed-source legacy
                                   tools).
  /proc/sys/kernel/ia32_emulation  Linux ≥ 6.7. 0 = disabled.
  /proc/sys/abi/legacy_va_layout 1 = force the old "high mmap"
                                   layout. Breaks PIE binaries.
  /proc/sys/fs/binfmt_misc/status  must be "enabled" for
                                   transparent invocation of
                                   foreign-ABI binaries (Wine,
                                   qemu-user, py-3.x via binfmt).

Why on a homelab :

* A distro upgrade silently flipping vsyscall32 → 0 breaks
  Steam and any 32-bit CUDA sample with `exit 1` and no error.
* A distro disabling ia32_emulation (Linux ≥ 6.7) breaks all
  32-bit binaries, often discovered weeks later.
* legacy_va_layout = 1 confuses PIE loaders and slows down
  some JITs.

Verdicts (priority order) :
  vsyscall32_disabled_breaks_steam   /proc/sys/abi/vsyscall32
                                       == 0.
  ia32_emulation_off                /proc/sys/kernel/
                                       ia32_emulation == 0.
  legacy_va_layout_forced           /proc/sys/abi/
                                       legacy_va_layout == 1.
  nonstandard_abi_quirks             binfmt_misc disabled OR
                                       any unknown /proc/sys/abi/
                                       knob with a non-default
                                       value.
  ok                                  all defaults.
  unknown                             /proc/sys/abi absent
                                       (non-x86 kernel build).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Dict, Optional


NAME = "abi_compat_audit"


_PROC_ABI = "/proc/sys/abi"
_PROC_KERNEL = "/proc/sys/kernel"
_PROC_BINFMT_STATUS = "/proc/sys/fs/binfmt_misc/status"


# /proc/sys/abi knobs we know about (key → expected default).
_KNOWN_ABI_DEFAULTS = {
    "vsyscall32": 1,
    "legacy_va_layout": 0,
    "x32_emulation": 1,
}


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


def scan_abi(proc_abi: str = _PROC_ABI) -> Dict[str, int]:
    out: Dict[str, int] = {}
    if not os.path.isdir(proc_abi):
        return out
    try:
        for n in os.listdir(proc_abi):
            full = os.path.join(proc_abi, n)
            if not os.path.isfile(full):
                continue
            v = _read_int(full)
            if v is not None:
                out[n] = v
    except OSError:
        return {}
    return out


def classify(abi_present: bool,
              abi_knobs: Dict[str, int],
              ia32_emulation: Optional[int],
              binfmt_status: Optional[str]) -> dict:
    if not abi_present:
        return {"verdict": "unknown",
                "reason": ("/proc/sys/abi absent — non-x86 kernel "
                          "or stripped sysctl tree."),
                "recommendation": ""}

    # 1) vsyscall32_disabled_breaks_steam
    v32 = abi_knobs.get("vsyscall32")
    if v32 == 0:
        return {"verdict": "vsyscall32_disabled_breaks_steam",
                "reason": ("/proc/sys/abi/vsyscall32 = 0 — "
                          "32-bit glibc binaries (Steam, "
                          "legacy CUDA samples) will exit "
                          "without explanation."),
                "recommendation": _recipe_vsyscall32()}

    # 2) ia32_emulation_off
    if ia32_emulation == 0:
        return {"verdict": "ia32_emulation_off",
                "reason": ("/proc/sys/kernel/ia32_emulation "
                          "= 0 — kernel rejects all 32-bit "
                          "binaries."),
                "recommendation": _recipe_ia32()}

    # 3) legacy_va_layout_forced
    if abi_knobs.get("legacy_va_layout") == 1:
        return {"verdict": "legacy_va_layout_forced",
                "reason": ("/proc/sys/abi/legacy_va_layout "
                          "= 1 — old high-mmap layout active. "
                          "Breaks PIE loaders / slows JITs."),
                "recommendation": _recipe_legacy_va()}

    # 4) nonstandard_abi_quirks — binfmt_misc disabled or odd
    if binfmt_status is not None and binfmt_status != "enabled":
        return {"verdict": "nonstandard_abi_quirks",
                "reason": (f"/proc/sys/fs/binfmt_misc/status = "
                          f"'{binfmt_status}'. Transparent "
                          f"invocation of foreign-ABI binaries "
                          f"(Wine, qemu-user, python-binfmt) "
                          f"broken."),
                "recommendation": _recipe_binfmt()}

    # Check for any unknown /proc/sys/abi knob with a non-default
    # value
    quirks = []
    for k, v in abi_knobs.items():
        if k not in _KNOWN_ABI_DEFAULTS:
            quirks.append(f"{k}={v}")
        elif v != _KNOWN_ABI_DEFAULTS[k]:
            quirks.append(f"{k}={v}")
    if quirks:
        return {"verdict": "nonstandard_abi_quirks",
                "reason": (f"Non-default /proc/sys/abi knob(s) : "
                          f"{', '.join(quirks[:5])}."),
                "recommendation": _recipe_quirks()}

    return {"verdict": "ok",
            "reason": (f"abi knobs : "
                      f"{', '.join(f'{k}={v}' for k, v in abi_knobs.items())} ; "
                      f"binfmt_misc={binfmt_status}."),
            "recommendation": ""}


def status(config=None,
            proc_abi: str = _PROC_ABI,
            proc_kernel: str = _PROC_KERNEL,
            proc_binfmt_status: str = _PROC_BINFMT_STATUS) -> dict:
    abi_present = os.path.isdir(proc_abi)
    abi_knobs = scan_abi(proc_abi)
    ia32_emulation = _read_int(os.path.join(
        proc_kernel, "ia32_emulation"))
    binfmt_status = _read(proc_binfmt_status)
    verdict = classify(abi_present, abi_knobs,
                          ia32_emulation, binfmt_status)
    return {"ok": abi_present,
              "abi_present": abi_present,
              "abi_knobs": abi_knobs,
              "ia32_emulation": ia32_emulation,
              "binfmt_misc_status": binfmt_status,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_vsyscall32() -> str:
    return ("# Re-enable vsyscall32 (needed by 32-bit glibc) :\n"
            "echo 1 | sudo tee /proc/sys/abi/vsyscall32\n"
            "echo 'abi.vsyscall32 = 1' \\\n"
            "  | sudo tee /etc/sysctl.d/99-vsyscall32.conf\n")


def _recipe_ia32() -> str:
    return ("# Re-enable 32-bit binary emulation :\n"
            "echo 1 | sudo tee /proc/sys/kernel/ia32_emulation\n"
            "echo 'kernel.ia32_emulation = 1' \\\n"
            "  | sudo tee /etc/sysctl.d/99-ia32.conf\n")


def _recipe_legacy_va() -> str:
    return ("# Restore modern VA layout (PIE-compatible) :\n"
            "echo 0 | sudo tee /proc/sys/abi/legacy_va_layout\n"
            "echo 'abi.legacy_va_layout = 0' \\\n"
            "  | sudo tee /etc/sysctl.d/99-va-layout.conf\n")


def _recipe_binfmt() -> str:
    return ("# Re-enable binfmt_misc (Wine, qemu-user, python) :\n"
            "echo 1 | sudo tee /proc/sys/fs/binfmt_misc/status\n"
            "# Or restart the systemd unit :\n"
            "sudo systemctl restart systemd-binfmt\n")


def _recipe_quirks() -> str:
    return ("# A /proc/sys/abi knob is at a non-default value.\n"
            "# Inspect :\n"
            "grep -r . /proc/sys/abi/\n"
            "# Reset to documented default via sysctl.d/.\n")

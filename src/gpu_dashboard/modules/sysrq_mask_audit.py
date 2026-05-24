"""Module sysrq_mask_audit — kernel SysRq + kexec_load
hardening posture (R&D #82.1).

The kernel SysRq channel (Alt-PrintScreen-<key>, or
``echo X > /proc/sysrq-trigger``) gives root-equivalent
control to anyone who can poke /proc/sysrq-trigger or has
console access. Default distros ship ``sysrq=438``-or-
``sysrq=1`` which exposes the full feature set ; homelab
users running open shells in tmux/console often forget
this.

Pairs with ``kexec_load_disabled`` for an integrity story :
a fully-enabled SysRq combined with kexec_load=allowed
means anyone with kernel write access can boot an arbitrary
kernel image without a reboot.

Bit map (kernel Documentation/admin-guide/sysrq.rst) :
  0x01  console log-level control
  0x02  keyboard (Secure Attention Key, unraw)         [risky]
  0x04  debugging dumps of processes / memory          [risky]
  0x08  sync command
  0x10  remount read-only
  0x20  signal processes (term, kill, OOM)
  0x40  reboot / poweroff
  0x80  nicing of all RT tasks

Verdicts (worst first) :

  sysrq_full_with_kexec   sysrq fully enabled
                          (value 1 or all-bits 255)
                          AND kexec_load_disabled = 0.
  sysrq_full_enabled      sysrq fully enabled (any path
                          to "all features available").
  sysrq_risky_subset      partial mask but includes SAK
                          (0x02) or dump (0x04) bits.
  ok                      0 (disabled) or only safe bits
                          (no SAK, no dump).
  unknown                 /proc/sys/kernel/sysrq missing.
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_KERNEL_ROOT = "/proc/sys/kernel"

# SysRq bits we treat as "risky" — can expose private state
# (dumps) or be used as a console attention key (SAK).
BIT_SAK = 0x02
BIT_DUMP = 0x04
_RISKY_BITS = BIT_SAK | BIT_DUMP

# Full-enable: value 1 means "all features", or the explicit
# all-ones mask 0xFF / 255.
_FULL_VALUES = frozenset({1, 255})


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    s = _read_text(path)
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def read_kernel(root: str = DEFAULT_KERNEL_ROOT) -> dict:
    return {
        "sysrq": _read_int(os.path.join(root, "sysrq")),
        "kexec_load_disabled": _read_int(
            os.path.join(root, "kexec_load_disabled")),
        # sysrq_always_enabled was a Ubuntu-specific patch ;
        # may not exist on stock kernels — None on absence.
        "sysrq_always_enabled": _read_int(
            os.path.join(root, "sysrq_always_enabled")),
    }


def _bits_set(mask: int) -> list[str]:
    """Returns labels of risky bits set in the mask."""
    out = []
    if mask & BIT_SAK:
        out.append("SAK (0x02)")
    if mask & BIT_DUMP:
        out.append("dump (0x04)")
    return out


def classify(values: dict) -> dict:
    sysrq = values.get("sysrq")
    if sysrq is None:
        return {"verdict": "unknown",
                "reason": "/proc/sys/kernel/sysrq missing."}

    kexec_disabled = values.get("kexec_load_disabled")
    is_full = sysrq in _FULL_VALUES

    # 1. err — full enable + kexec_load not locked
    if is_full and (kexec_disabled is None
                     or kexec_disabled == 0):
        return {"verdict": "sysrq_full_with_kexec",
                "reason": (
                    f"sysrq = {sysrq} (fully enabled) and "
                    "kexec_load_disabled = 0 — an attacker "
                    "with kernel write or console can boot "
                    "an arbitrary kernel without reboot."),
                "sysrq": sysrq,
                "kexec_load_disabled": kexec_disabled}

    # 2. warn — full enable (kexec locked)
    if is_full:
        return {"verdict": "sysrq_full_enabled",
                "reason": (
                    f"sysrq = {sysrq} (fully enabled). "
                    "Any process with /proc/sysrq-trigger "
                    "write can issue dumps, reboots, "
                    "OOM-kill, etc."),
                "sysrq": sysrq}

    # 3. accent — partial mask with risky bits
    if sysrq != 0:
        risky = _bits_set(sysrq)
        if risky:
            return {"verdict": "sysrq_risky_subset",
                    "reason": (
                        f"sysrq = {sysrq} mask includes "
                        f"risky bit(s): {','.join(risky)}."),
                    "sysrq": sysrq,
                    "risky_bits": risky}

    # 4. ok
    if sysrq == 0:
        reason = "sysrq = 0 (fully disabled)."
    else:
        reason = (
            f"sysrq = {sysrq} — safe subset, no SAK or "
            "dump bits set.")
    return {"verdict": "ok", "reason": reason,
            "sysrq": sysrq}


def status(config: Optional[dict] = None,
           kernel_root: str = DEFAULT_KERNEL_ROOT) -> dict:
    values = read_kernel(kernel_root)
    verdict = classify(values)
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "sysrq_full_with_kexec"),
        "values": values,
        "verdict": verdict,
    }

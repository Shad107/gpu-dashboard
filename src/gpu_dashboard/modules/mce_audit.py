"""Module mce_audit — Machine Check Exception layer auditor (R&D #47.4).

Reads /sys/devices/system/machinecheck/machinecheck<N>/{
check_interval, cmci_disabled, ignore_ce, dont_log_ce,
monarch_timeout, tolerant, bank0..bank<M>} — one directory per
logical CPU with mirrored attributes.

Distinct from shipped edac_ram_ecc (DIMM-level CE/UE counts with
EDAC row/column attribution) — this is the *MCE-machinery-layer*
side : is the kernel actually *receiving* the corrected memory
errors that EDAC then attributes, or is the firmware / userspace
silencing them?

  check_interval     poll interval seconds (300 default).
  cmci_disabled      0 = use CMCI interrupts, 1 = polling only.
                     1 on Intel desktops is a 300s blind spot.
  ignore_ce          1 = kernel silently drops every corrected
                     error before EDAC sees it. Some Dell
                     PowerEdge BIOSes set this from firmware to
                     "improve uptime metrics".
  dont_log_ce        1 = log nothing about corrected errors (some
                     kernels expose this instead of ignore_ce).
  tolerant           0=panic on UE, 1=panic if critical, 2=keep
                     running with SIGBUS, 3=ignore everything.
                     A user who copy-pasted 'echo 3 >
                     /sys/.../tolerant' from a Reddit "stop the
                     kernel panicking" thread is silently
                     corrupting RAM during inference.
  bank0..bankN       per-MCE-bank enable mask. Mask=0 means that
                     bank's errors are completely ignored. Often
                     abused to silence noisy DRAM channels rather
                     than replacing the DIMM.

Verdicts (priority-ordered) :
  ignore_ce_masked       ignore_ce=1 OR dont_log_ce=1 → CE never
                         reaches EDAC. Shipped edac_ram_ecc card
                         showing zero CE could be a lie.
  tolerant_too_high      tolerant ≥ 2 → kernel keeps running
                         through uncorrectable errors. Silent
                         data corruption risk during inference.
  cmci_disabled_intel    cmci_disabled=1 → up to 300s blind spot
                         between CE events. Acceptable on AMD
                         EPYC (known CMCI errata) but suspicious
                         on Intel.
  bank_silenced          ≥1 bank mask=0 (typical bank 4 = DRAM
                         channel) → that bank's events ignored.
  ok                     sane defaults.
  no_mce                 /sys/devices/system/machinecheck absent
                         (CONFIG_X86_MCE=n or non-x86 arch).
  unknown                directory present but unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "mce_audit"


_SYS_MCE = "/sys/devices/system/machinecheck"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    s = t.strip()
    try:
        return int(s, 0)  # auto-base for '0x...' or decimal
    except ValueError:
        pass
    # Bare-hex without 0x prefix (how machinecheck<N>/bank<N> renders).
    try:
        return int(s, 16)
    except ValueError:
        return None


def list_cpus(sys_mce: str = _SYS_MCE) -> list:
    if not os.path.isdir(sys_mce):
        return []
    out: list = []
    for name in os.listdir(sys_mce):
        if re.match(r"^machinecheck\d+$", name):
            out.append(int(name[len("machinecheck"):]))
    out.sort()
    return out


_INT_FIELDS = (
    "check_interval", "cmci_disabled", "ignore_ce",
    "dont_log_ce", "monarch_timeout", "tolerant", "print_all",
)


def read_cpu_mce(sys_mce: str, cpu: int) -> dict:
    d = os.path.join(sys_mce, f"machinecheck{cpu}")
    out: dict = {"cpu": cpu}
    for f in _INT_FIELDS:
        v = _read_int(os.path.join(d, f))
        if v is not None:
            out[f] = v
    # Banks : enumerate bank0..bankN.
    banks: dict = {}
    try:
        names = os.listdir(d)
    except OSError:
        names = []
    for n in names:
        m = re.match(r"^bank(\d+)$", n)
        if not m:
            continue
        idx = int(m.group(1))
        v = _read_int(os.path.join(d, n))
        if v is not None:
            banks[idx] = v
    out["banks"] = banks
    return out


def is_uniform(rows: list, field: str) -> bool:
    """All CPUs report the same value for this field (MCE knobs
    should be uniform — kernel mirrors writes across CPUs)."""
    values = {r.get(field) for r in rows if field in r}
    return len(values) <= 1


_RECIPE_IGNORE_CE = (
    "# CE-masking enabled — kernel never sees corrected memory\n"
    "# errors. The EDAC card may falsely report 'ecc_clean' :\n"
    "echo 0 | sudo tee /sys/devices/system/machinecheck/machinecheck0/ignore_ce\n"
    "echo 0 | sudo tee /sys/devices/system/machinecheck/machinecheck0/dont_log_ce\n"
    "# Kernel mirrors writes to every CPU automatically.\n"
    "# Investigate BIOS for 'Memory Error Reporting' / 'CE\n"
    "# Suppress' — some Dell PowerEdge firmware sets this."
)

_RECIPE_TOLERANT_HIGH = (
    "# tolerant ≥ 2 → kernel silently keeps running through\n"
    "# UNCORRECTABLE errors. Bring it back to 1 (panic on critical) :\n"
    "echo 1 | sudo tee /sys/devices/system/machinecheck/machinecheck0/tolerant\n"
    "# 0 = panic on any UE (safest for data integrity)\n"
    "# 1 = panic on critical UE (default, balanced)\n"
    "# 2/3 = silent data corruption risk"
)

_RECIPE_CMCI_INTEL = (
    "# cmci_disabled=1 on what appears to be Intel — likely a\n"
    "# legacy workaround that's no longer needed. Re-enable :\n"
    "echo 0 | sudo tee /sys/devices/system/machinecheck/machinecheck0/cmci_disabled"
)

_RECIPE_BANK_SILENCED = (
    "# Per-MCE-bank mask = 0 — that bank's events are ignored.\n"
    "# Often a workaround for noisy DIMM. Inspect :\n"
    "for b in /sys/devices/system/machinecheck/machinecheck0/bank*; do\n"
    "  echo \"$b = $(cat $b)\"\n"
    "done\n"
    "# To re-enable bank 4 (typically DRAM) :\n"
    "echo 0xffffffff | sudo tee /sys/devices/system/machinecheck/machinecheck0/bank4\n"
    "# Then watch dmesg for the underlying error rate."
)


def classify(rows: list) -> dict:
    if not rows:
        return {"verdict": "no_mce",
                "reason": ("/sys/devices/system/machinecheck "
                           "absent — CONFIG_X86_MCE=n or non-x86."),
                "recommendation": ""}
    head = rows[0]
    ignore_ce = head.get("ignore_ce", 0) or head.get("dont_log_ce", 0)
    if ignore_ce:
        return {"verdict": "ignore_ce_masked",
                "reason": ("MCE corrected-error logging is disabled "
                           "via ignore_ce or dont_log_ce — EDAC "
                           "card may falsely report 'ecc_clean'."),
                "recommendation": _RECIPE_IGNORE_CE}
    tolerant = head.get("tolerant")
    if isinstance(tolerant, int) and tolerant >= 2:
        return {"verdict": "tolerant_too_high",
                "reason": (f"tolerant={tolerant} — kernel keeps "
                           f"running through uncorrectable errors. "
                           f"Silent data corruption risk."),
                "recommendation": _RECIPE_TOLERANT_HIGH}
    if head.get("cmci_disabled", 0) == 1:
        return {"verdict": "cmci_disabled_intel",
                "reason": ("cmci_disabled=1 — kernel polls every "
                           "300s instead of receiving CMCI "
                           "interrupts. Blind spot up to 300s."),
                "recommendation": _RECIPE_CMCI_INTEL}
    silenced: list = []
    for idx, mask in (head.get("banks") or {}).items():
        if mask == 0:
            silenced.append(idx)
    if silenced:
        return {"verdict": "bank_silenced",
                "reason": (f"MCE bank(s) {silenced} have mask=0 — "
                           f"events ignored. Often a workaround "
                           f"for a noisy DIMM that should be "
                           f"replaced."),
                "recommendation": _RECIPE_BANK_SILENCED}
    return {"verdict": "ok",
            "reason": (f"{len(rows)} CPU(s) ; ignore_ce=0, "
                       f"cmci_disabled={head.get('cmci_disabled')}, "
                       f"tolerant={tolerant if tolerant is not None else 'n/a'}, "
                       f"check_interval="
                       f"{head.get('check_interval')}s."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_MCE):
        return {
            "ok": False,
            "verdict": {"verdict": "no_mce",
                         "reason": ("/sys/devices/system/machinecheck "
                                    "absent."),
                         "recommendation": ""},
            "cpu_count": 0, "cpus": [],
        }
    cpus = list_cpus(_SYS_MCE)
    rows = [read_cpu_mce(_SYS_MCE, c) for c in cpus]
    # MCE attributes mirror across CPUs in the kernel ; we report
    # CPU 0 as representative + flag any drift.
    uniform_check = is_uniform(rows, "ignore_ce")
    verdict = classify(rows)
    return {
        "ok": bool(rows),
        "cpu_count": len(cpus),
        "uniform_across_cpus": uniform_check,
        "cpu0_state": rows[0] if rows else {},
        "verdict": verdict,
    }

"""Module vm_compaction_proactive_audit — proactive compaction
+ pagelist drain knobs (R&D #105.4).

The existing vm_sysctl_audit / vm_tuning_deep / buddyinfo_frag /
pagetypeinfo_audit / page_owner_frag_audit cover fragmentation
*symptoms* and `extfrag_threshold`. None audit the proactive-
compaction knobs added in 5.9+ :

  vm.compaction_proactiveness          0..100 ; default 20.
                                       0 disables background
                                       compactor.
  vm.compact_unevictable_allowed       1 = allow compacting
                                       mlock'd pages (default).
  vm.percpu_pagelist_high_fraction     0 = kernel auto-sizes ;
                                       1-7 forces tiny per-cpu
                                       pagelists → frequent
                                       drains → IPI bursts.

THP cross-check via /sys/kernel/mm/transparent_hugepage/enabled.

Verdicts (worst-first) :

  proactive_off_thp_always       warn   compaction_proactiveness
                                        = 0 AND THP=always —
                                        khugepaged has to do
                                        all the work, stalls.
  proactive_aggressive_jank      accent proactiveness >= 50 —
                                        can cause sched-IPI
                                        bursts on 8c-class CPUs.
  pagelist_fraction_extreme      accent percpu_pagelist_high_
                                        fraction in 1..7 —
                                        very low, frequent
                                        drains.
  compact_unevictable_disabled   accent compact_unevictable_
                                        allowed=0 — mlock'd
                                        pages immobile, can
                                        amplify fragmentation.
  ok                                    sane defaults.
  requires_root                         sysctls unreadable.
  unknown                               /proc/sys/vm absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "vm_compaction_proactive_audit"

DEFAULT_VM = "/proc/sys/vm"
DEFAULT_THP = (
    "/sys/kernel/mm/transparent_hugepage/enabled")


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


def parse_thp_enabled(text: Optional[str]
                       ) -> Optional[str]:
    """'[always] madvise never' → 'always'."""
    if not text:
        return None
    for tok in text.split():
        if tok.startswith("[") and tok.endswith("]"):
            return tok[1:-1]
    return None


def classify(vm_present: bool,
             proactiveness: Optional[int],
             compact_unevictable: Optional[int],
             pagelist_fraction: Optional[int],
             thp_active: Optional[str]) -> dict:
    if not vm_present:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/vm absent — kernel without "
                    "/proc/sys.")}
    if (proactiveness is None
            and compact_unevictable is None
            and pagelist_fraction is None):
        return {"verdict": "requires_root",
                "reason": (
                    "vm.compaction_* sysctls unreadable "
                    "— re-run as root.")}

    # warn — proactiveness=0 AND THP=always
    if (proactiveness == 0
            and thp_active == "always"):
        return {
            "verdict": "proactive_off_thp_always",
            "reason": (
                "vm.compaction_proactiveness=0 AND THP="
                "always — khugepaged has to do all the "
                "compaction work synchronously ; expect "
                "stalls during model load.")}

    # accent — proactiveness very high
    if proactiveness is not None and proactiveness >= 50:
        return {
            "verdict": "proactive_aggressive_jank",
            "reason": (
                f"vm.compaction_proactiveness="
                f"{proactiveness} (>= 50) — background "
                "compactor very aggressive ; sched-IPI "
                "bursts on smaller CPUs.")}

    # accent — pagelist_fraction extremely low (forced)
    if (pagelist_fraction is not None
            and 1 <= pagelist_fraction <= 7):
        return {
            "verdict": "pagelist_fraction_extreme",
            "reason": (
                f"vm.percpu_pagelist_high_fraction="
                f"{pagelist_fraction} — very low forces "
                "frequent per-cpu pagelist drains ; "
                "default 0 lets kernel auto-size.")}

    # accent — compact_unevictable disabled
    if compact_unevictable == 0:
        return {
            "verdict": "compact_unevictable_disabled",
            "reason": (
                "vm.compact_unevictable_allowed=0 — "
                "mlock'd pages immobile during compaction. "
                "Amplifies fragmentation on long-uptime "
                "boxes.")}

    return {"verdict": "ok",
            "reason": (
                f"proactiveness={proactiveness} ; "
                f"compact_unevictable={compact_unevictable} "
                f"; pagelist_fraction={pagelist_fraction} ; "
                f"THP={thp_active}. Sane.")}


def status(config: Optional[dict] = None,
           vm: str = DEFAULT_VM,
           thp_path: str = DEFAULT_THP) -> dict:
    vm_present = os.path.isdir(vm)
    proactiveness = (
        _read_int(os.path.join(vm,
                                "compaction_proactiveness"))
        if vm_present else None)
    compact_unevictable = (
        _read_int(os.path.join(
            vm, "compact_unevictable_allowed"))
        if vm_present else None)
    pagelist_fraction = (
        _read_int(os.path.join(
            vm, "percpu_pagelist_high_fraction"))
        if vm_present else None)
    thp_active = parse_thp_enabled(_read_text(thp_path))
    verdict = classify(vm_present, proactiveness,
                       compact_unevictable,
                       pagelist_fraction, thp_active)
    return {
        "ok": verdict["verdict"] == "ok",
        "compaction_proactiveness": proactiveness,
        "compact_unevictable_allowed": compact_unevictable,
        "percpu_pagelist_high_fraction": pagelist_fraction,
        "thp_enabled": thp_active,
        "verdict": verdict,
    }

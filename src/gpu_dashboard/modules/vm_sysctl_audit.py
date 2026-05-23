"""Module vm_sysctl_audit — VM sysctl LLM-rig sanity (R&D #32.4).

Every fresh Linux kernel ships with /proc/sys/vm/swappiness=60. For
a desktop where the user actively switches between apps that's a
fine compromise — for an LLM rig where the user *wants* their 20-GiB
GGUF mmap'd resident, swappiness=60 is the upstream cause of the
"why is my model swapping despite --mlock" pain that #29.8 rlimit
auditor catches at the symptom and #31.2 smaps_rollup catches at
the consequence.

This module audits ~7-9 vm.* sysctls against an LLM-rig baseline and
emits a single sysctl.d Drop-In with the recommended values:

  swappiness               default 60, ideal 1-10 for LLM rigs
  vfs_cache_pressure       default 100, optional 50 for hotter
                           dentry cache
  overcommit_memory        default 0 (heuristic) OK ; 2 (strict) is
                           a foot-gun that breaks llama-server on
                           large prompt batches
  zone_reclaim_mode        default 0 (off) ; >0 means NUMA-local
                           reclaim, hurts inference cache locality
  overcommit_ratio         informational
  dirty_background_ratio   informational
  dirty_ratio              informational
  min_free_kbytes          informational

Each non-ok row contributes to a single Drop-In recipe at
/etc/sysctl.d/99-llm.conf which the user can paste-and-reload via
`sudo sysctl --system`.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "vm_sysctl_audit"


_SYSCTL_ROOT = "/proc/sys/vm"


# Canonical knobs we audit. recommended_for_llm = None → informational
# only; we still report the value but don't flag default as warn.
_KNOBS: dict = {
    "swappiness": {
        "recommended": 10,
        "ok_range": (0, 10),
        "warn_above": 10,
        "reason": ("LLM rigs want their mmap'd GGUF + KV cache to stay "
                    "resident. The default 60 actively swaps anon pages "
                    "even when free RAM exists."),
    },
    "vfs_cache_pressure": {
        "recommended": 50,
        "ok_range": (1, 100),
        "warn_above": 200,
        "reason": ("Lower keeps the dentry/inode cache hotter, which "
                    "matters for `mmap()` repeatedly touching the "
                    "same GGUF dentries."),
    },
    "overcommit_memory": {
        "recommended": 0,
        "ok_values": (0, 1),
        "warn_values": (2,),
        "reason": ("Strict overcommit (mode=2) refuses mallocs once "
                    "RAM*ratio is reached. llama.cpp prompt-processing "
                    "blows past that on long contexts."),
    },
    "zone_reclaim_mode": {
        "recommended": 0,
        "ok_values": (0,),
        "warn_values": (1, 2, 3),
        "reason": ("Non-zero forces NUMA-local reclaim, which evicts "
                    "hot LLM pages from other zones rather than using "
                    "remote DRAM."),
    },
    "overcommit_ratio": {
        "recommended": None,    # informational
        "reason": "Cap on RAM*ratio when overcommit_memory=2.",
    },
    "dirty_background_ratio": {
        "recommended": None,
        "reason": "Async writeback threshold (% of dirtyable memory).",
    },
    "dirty_ratio": {
        "recommended": None,
        "reason": "Sync writeback throttle threshold.",
    },
    "min_free_kbytes": {
        "recommended": None,
        "reason": "Kernel free-memory reserve.",
    },
}


def read_sysctl(root: str, name: str) -> Optional[int]:
    p = os.path.join(root, name)
    try:
        with open(p) as f:
            s = f.read().strip()
    except OSError:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def classify_one(name: str, value: Optional[int]) -> dict:
    spec = _KNOBS.get(name)
    if not spec:
        return {"name": name, "value": value, "severity": "unknown",
                "reason": "Unknown sysctl key.", "recommended": None}
    if value is None:
        return {"name": name, "value": None, "severity": "unknown",
                "reason": "Sysctl not present on this kernel.",
                "recommended": spec.get("recommended")}
    rec = spec.get("recommended")
    if rec is None:
        # informational — never warn
        return {"name": name, "value": value, "severity": "ok",
                "reason": spec.get("reason", ""), "recommended": None}
    # explicit value-set match (overcommit_memory, zone_reclaim_mode)
    if "warn_values" in spec and value in spec["warn_values"]:
        return {"name": name, "value": value, "severity": "warn",
                "reason": spec.get("reason", ""), "recommended": rec}
    if "ok_values" in spec and value in spec["ok_values"]:
        return {"name": name, "value": value, "severity": "ok",
                "reason": "Within LLM-rig OK set.", "recommended": rec}
    # range-based (swappiness, vfs_cache_pressure)
    lo, hi = spec.get("ok_range", (None, None))
    if lo is not None and hi is not None and lo <= value <= hi:
        return {"name": name, "value": value, "severity": "ok",
                "reason": "Within LLM-rig OK range.", "recommended": rec}
    return {"name": name, "value": value, "severity": "warn",
            "reason": spec.get("reason", ""), "recommended": rec}


def aggregate(rows: list) -> str:
    if not rows:
        return "unknown"
    sevs = {r["severity"] for r in rows}
    if "warn" in sevs:
        return "warn"
    if "ok" in sevs:
        return "ok"
    return "unknown"


def make_recipe(flagged: list) -> str:
    if not flagged:
        return ""
    lines = [
        "# /etc/sysctl.d/99-llm.conf — LLM-rig vm.* tuning",
        "# Generated by gpu-dashboard, R&D #32.4 vm_sysctl_audit",
    ]
    for f in flagged:
        lines.append(f"vm.{f['name']}={f['recommended']}")
    lines.append("")
    lines.append("# Apply now without reboot:")
    lines.append("sudo sysctl --system")
    return "\n".join(lines)


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYSCTL_ROOT):
        return {"ok": False, "error": "sysctl_unavailable",
                "reason": f"{_SYSCTL_ROOT} not present."}
    rows: list = []
    for name in _KNOBS:
        v = read_sysctl(_SYSCTL_ROOT, name)
        if v is None:
            # Skip absent sysctls so partial /proc/sys/vm doesn't
            # cause a wall of unknown rows.
            continue
        rows.append(classify_one(name, v))
    flagged = [r for r in rows
                if r["severity"] == "warn" and r.get("recommended") is not None]
    return {
        "ok": True,
        "row_count": len(rows),
        "rows": rows,
        "worst_severity": aggregate(rows),
        "flagged_count": len(flagged),
        "recipe": make_recipe(flagged),
    }

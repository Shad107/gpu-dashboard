"""Module resctrl_audit — Intel/AMD resctrl L3/MBA allocation
and partitioning detector (R&D #90.1).

resctrl is the Linux interface to RDT/QoS (Intel Resource
Director Technology / AMD QoS) — Cache Allocation Technology
(CAT) and Memory Bandwidth Allocation (MBA). When mounted at
/sys/fs/resctrl, an admin can carve L3 cache ways away from
specific CPUs or throttle their memory bandwidth. This is
rare on desktops but when it IS active it silently caps
LLM-inference / dataloader throughput in ways nvidia-smi
cannot see.

No existing module reads /sys/fs/resctrl :

  * cpu_cache_topology reads /sys/devices/system/cpu/cache/
    index*/ static layout only.
  * perf_pmu_audit reads uncore_cha / imc PMUs.
  * Nothing touches the allocation/monitoring tree.

Reads :

  /sys/fs/resctrl/                    mount root
  /sys/fs/resctrl/info/L3/            CAT support indicator
  /sys/fs/resctrl/info/MB/            MBA support indicator
  /sys/fs/resctrl/schemata            default group's
                                      L3:0=ffff / MB:0=100 etc
  /sys/fs/resctrl/<group>/schemata    each CTRL group

Verdicts (worst-first) :

  cat_partitioned_non_default  err  any CTRL group has an L3
                                    bitmask whose hex value
                                    differs from the default
                                    group's — cache ways are
                                    carved out from some CPUs.
  mba_throttle_active          warn any MB value < 100 in any
                                    group's schemata — memory
                                    bandwidth throttled.
  resctrl_mounted_unused       accent /sys/fs/resctrl mounted
                                      but only default group
                                      present (no benefit,
                                      blocks dynamic use).
  ok                          resctrl present, no
                              partitioning/throttle active.
  requires_root               /sys/fs/resctrl/schemata
                              mode-700 (rare).
  unknown                     /sys/fs/resctrl absent or
                              kernel built without
                              CONFIG_X86_CPU_RESCTRL (typical
                              on desktops).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "resctrl_audit"

DEFAULT_RESCTRL_ROOT = "/sys/fs/resctrl"

_L3_LINE = re.compile(r"^L3:(.+)$", re.MULTILINE)
_MB_LINE = re.compile(r"^MB:(.+)$", re.MULTILINE)


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_schemata(text: str) -> dict:
    """Parse a schemata file. Returns {'L3': {domain: mask},
    'MB': {domain: pct}}."""
    out: dict = {"L3": {}, "MB": {}}
    if not text:
        return out
    for resource, regex in (("L3", _L3_LINE), ("MB", _MB_LINE)):
        m = regex.search(text)
        if not m:
            continue
        for tok in m.group(1).split(";"):
            tok = tok.strip()
            if "=" not in tok:
                continue
            dom, val = tok.split("=", 1)
            out[resource][dom.strip()] = val.strip()
    return out


def _ctrl_groups(root: str) -> list:
    """Return names of CTRL groups (top-level subdirs other
    than info / mon_groups / mon_data)."""
    if not os.path.isdir(root):
        return []
    try:
        entries = os.listdir(root)
    except OSError:
        return []
    out: list = []
    for name in entries:
        if name in ("info", "mon_groups", "mon_data"):
            continue
        path = os.path.join(root, name)
        if (os.path.isdir(path)
                and os.path.isfile(
                    os.path.join(path, "schemata"))):
            out.append(name)
    return sorted(out)


def _default_schemata(root: str) -> Optional[str]:
    return _read_text(os.path.join(root, "schemata"))


def classify(root_present: bool,
             default_text: Optional[str],
             ctrl_groups: list,
             group_schemata: dict) -> dict:
    if not root_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/fs/resctrl absent — kernel built "
                    "without CONFIG_X86_CPU_RESCTRL or "
                    "resctrl not mounted (typical on desktops).")}

    if default_text is None:
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/fs/resctrl/schemata unreadable — "
                    "re-run as root (rare).")}

    default = parse_schemata(default_text)

    # err — CAT partitioning vs default
    default_l3 = default.get("L3", {})
    for gname in ctrl_groups:
        gtext = group_schemata.get(gname, "")
        if not gtext:
            continue
        g = parse_schemata(gtext)
        for dom, mask in g.get("L3", {}).items():
            d_mask = default_l3.get(dom)
            if d_mask is not None and mask != d_mask:
                return {
                    "verdict": "cat_partitioned_non_default",
                    "reason": (
                        f"CTRL group '{gname}' has L3 mask "
                        f"{mask} for domain {dom} vs default "
                        f"{d_mask} — cache ways carved out."),
                    "group": gname,
                    "mask": mask,
                }

    # warn — MBA throttle active
    for src_name, parsed in (
            ("default", default),
            *((g, parse_schemata(group_schemata.get(g, "")))
              for g in ctrl_groups)):
        for dom, pct in parsed.get("MB", {}).items():
            try:
                pct_i = int(pct)
            except ValueError:
                continue
            if pct_i < 100:
                return {
                    "verdict": "mba_throttle_active",
                    "reason": (
                        f"MBA group '{src_name}' has MB={pct} "
                        f"% on domain {dom} — memory bandwidth "
                        "throttled below 100%."),
                    "group": src_name,
                    "mb_pct": pct_i,
                }

    # accent — mounted but no extra groups
    if not ctrl_groups:
        return {"verdict": "resctrl_mounted_unused",
                "reason": (
                    "/sys/fs/resctrl mounted but only default "
                    "group present — no allocations active, "
                    "but the mount keeps the resctrl FS "
                    "machinery resident.")}

    return {"verdict": "ok",
            "reason": (
                f"resctrl mounted, {len(ctrl_groups)} CTRL "
                "group(s) ; no CAT partitioning or MBA "
                "throttle active.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_RESCTRL_ROOT) -> dict:
    root_present = os.path.isdir(root)
    default_text = (
        _default_schemata(root) if root_present else None)
    groups = _ctrl_groups(root) if root_present else []
    group_schemata = {
        g: _read_text(os.path.join(root, g, "schemata")) or ""
        for g in groups
    }
    verdict = classify(root_present, default_text, groups,
                       group_schemata)
    return {
        "ok": verdict["verdict"] == "ok",
        "mounted": root_present,
        "ctrl_group_count": len(groups),
        "verdict": verdict,
    }

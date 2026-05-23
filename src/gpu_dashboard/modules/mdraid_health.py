"""Module mdraid_health — Linux software RAID auditor (R&D #45.2).

Parses /proc/mdstat — the user-facing summary of every active md*
device — and reads /sys/block/md*/md/* for per-array detail :

  /proc/mdstat                            free-form text. Per
                                          array : name, level,
                                          active disks, total disks,
                                          state flags [_U or [UU]
                                          markers, and an optional
                                          resync / recovery line.
  /sys/block/md<N>/md/array_state         "clean" / "active" /
                                          "readonly" / "inactive".
  /sys/block/md<N>/md/sync_action         "idle" / "resync" /
                                          "check" / "repair" /
                                          "recover".
  /sys/block/md<N>/md/sync_speed          current resync speed
                                          KB/s (0 if idle).
  /sys/block/md<N>/md/mismatch_cnt        non-zero after a check
                                          → silent corruption!
  /sys/block/md<N>/md/degraded            > 0 if running with
                                          fewer disks than the
                                          layout requires.

Verdicts (priority-ordered) :
  degraded            ≥1 array has degraded > 0 OR /proc/mdstat
                      shows _U / U_ pattern → run on fewer disks
                      than the layout expects.
  mismatch_present    ≥1 array has mismatch_cnt > 0 → previous
                      `check` action found a silent-corruption
                      discrepancy ; investigate (likely a bad
                      member disk).
  resyncing           ≥1 array currently in resync/recover —
                      performance impact warning, not a fault.
  ok                  arrays exist and all clean.
  no_arrays           Personalities loaded but no active md* —
                      nothing to audit.
  unknown             /proc/mdstat unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "mdraid_health"


_PROC_MDSTAT = "/proc/mdstat"
_SYS_BLOCK = "/sys/block"


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
    try:
        return int(t.strip())
    except ValueError:
        return None


_MD_HEADER_RE = re.compile(
    r"^(md\d+)\s*:\s*(\w+)\s+(\w+)\s+(.+)$"
)


def parse_mdstat(text: str) -> dict:
    """Return {personalities: [...], arrays: [{name, state,
    level, members, marker, resync}]}."""
    out: dict = {"personalities": [], "arrays": []}
    if not text:
        return out
    lines = text.splitlines()
    cur: Optional[dict] = None
    for line in lines:
        if line.startswith("Personalities"):
            out["personalities"] = re.findall(r"\[(\w+)\]", line)
            continue
        if line.startswith("unused devices"):
            continue
        m = _MD_HEADER_RE.match(line)
        if m:
            if cur is not None:
                out["arrays"].append(cur)
            cur = {
                "name": m.group(1),
                "state": m.group(2),
                "level": m.group(3),
                "members": m.group(4).split(),
                "marker": "",
                "resync": None,
            }
            continue
        if cur is None:
            continue
        # Look for "[N/M] [UUU_]" status line or "blocks super..."
        marker_match = re.search(r"\[([U_]+)\]", line)
        if marker_match:
            cur["marker"] = marker_match.group(1)
        if "resync" in line or "recovery" in line or "check" in line:
            cur["resync"] = line.strip()
    if cur is not None:
        out["arrays"].append(cur)
    return out


def read_array_sysfs(sys_block: str, name: str) -> dict:
    """Best-effort sysfs read for one md<N>."""
    md = os.path.join(sys_block, name, "md")
    if not os.path.isdir(md):
        return {}
    return {
        "array_state": (_read(os.path.join(md, "array_state"))
                          or "").strip() or None,
        "sync_action": (_read(os.path.join(md, "sync_action"))
                          or "").strip() or None,
        "sync_speed": _read_int(os.path.join(md, "sync_speed")),
        "mismatch_cnt": _read_int(os.path.join(md, "mismatch_cnt")),
        "degraded": _read_int(os.path.join(md, "degraded")),
    }


_RECIPE_DEGRADED = (
    "# RAID array running degraded — fewer member disks than the\n"
    "# layout requires. Inspect :\n"
    "cat /proc/mdstat\n"
    "sudo mdadm --detail /dev/md<N>\n"
    "# Replace the failed disk + add it back :\n"
    "sudo mdadm --add /dev/md<N> /dev/<NEW-DISK>\n"
    "# Watch the rebuild :\n"
    "watch -n 5 'cat /proc/mdstat'"
)

_RECIPE_MISMATCH = (
    "# Previous mdadm `check` found a mismatch between mirror copies\n"
    "# or parity calculations — likely a bad member disk silently\n"
    "# returning wrong data. Investigate :\n"
    "sudo mdadm --detail /dev/md<N>\n"
    "sudo smartctl -a /dev/<MEMBER>   # check each member's SMART\n"
    "# After identifying the culprit, fail + replace it :\n"
    "sudo mdadm --fail /dev/md<N> /dev/<BAD>\n"
    "sudo mdadm --remove /dev/md<N> /dev/<BAD>\n"
    "sudo mdadm --add /dev/md<N> /dev/<NEW>"
)

_RECIPE_RESYNCING = (
    "# An array is currently in resync / recover / check. Expected\n"
    "# during initial sync, after a member-add, or scheduled scrub.\n"
    "# Performance impact is throttled by :\n"
    "cat /proc/sys/dev/raid/speed_limit_max\n"
    "cat /proc/sys/dev/raid/speed_limit_min\n"
    "# If you need the rebuild to finish faster, raise the floor :\n"
    "echo 200000 | sudo tee /proc/sys/dev/raid/speed_limit_min"
)


def classify(arrays: list) -> dict:
    if not arrays:
        return {"verdict": "no_arrays",
                "reason": ("/proc/mdstat shows personalities loaded "
                           "but no active md* arrays — nothing to "
                           "audit. Fine on hosts that don't use "
                           "software RAID."),
                "recommendation": ""}
    degraded = [a for a in arrays
                  if (a.get("sysfs", {}).get("degraded") or 0) > 0
                  or "_" in (a.get("marker") or "")]
    if degraded:
        names = ", ".join(
            f"{a['name']} ({a.get('level')}, marker={a.get('marker') or '?'})"
            for a in degraded)
        return {"verdict": "degraded",
                "reason": (f"{len(degraded)} array(s) running "
                           f"degraded. {names}"),
                "recommendation": _RECIPE_DEGRADED}
    mismatch = [a for a in arrays
                  if (a.get("sysfs", {}).get("mismatch_cnt") or 0) > 0]
    if mismatch:
        names = ", ".join(
            f"{a['name']} (mismatch={a['sysfs']['mismatch_cnt']})"
            for a in mismatch)
        return {"verdict": "mismatch_present",
                "reason": (f"{len(mismatch)} array(s) have non-zero "
                           f"mismatch_cnt — silent corruption "
                           f"flagged by previous check. {names}"),
                "recommendation": _RECIPE_MISMATCH}
    resync = [a for a in arrays
                if (a.get("sysfs", {}).get("sync_action") or "idle")
                != "idle" or a.get("resync")]
    if resync:
        names = ", ".join(
            f"{a['name']} ({a.get('sysfs', {}).get('sync_action') or 'syncing'})"
            for a in resync)
        return {"verdict": "resyncing",
                "reason": (f"{len(resync)} array(s) in resync / "
                           f"recover / check. {names}"),
                "recommendation": _RECIPE_RESYNCING}
    return {"verdict": "ok",
            "reason": (f"{len(arrays)} md array(s) ; all clean, "
                       f"idle, no mismatch."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    text = _read(_PROC_MDSTAT)
    if text is None:
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": "/proc/mdstat unreadable.",
                         "recommendation": ""},
            "personalities": [], "arrays": [],
        }
    parsed = parse_mdstat(text)
    for a in parsed["arrays"]:
        a["sysfs"] = read_array_sysfs(_SYS_BLOCK, a["name"])
    verdict = classify(parsed["arrays"])
    return {
        "ok": True,
        "personalities": parsed["personalities"],
        "array_count": len(parsed["arrays"]),
        "arrays": parsed["arrays"],
        "verdict": verdict,
    }

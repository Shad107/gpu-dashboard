"""Module swap_priority_tiering_audit — /proc/swaps priority
column posture (R&D #110.1).

swap_tunables_audit reads /proc/swaps for path / rotational
detection but never parses the Priority column (grep-verified).
Higher priority swaps fill first ; equal-priority swaps round-
robin. Foot-gun on zram+disk setups: if the disk swap has
*higher* priority than the zram swap, cold pages skip zram and
go straight to disk — defeating the whole point of zram.

Reads :

  /proc/swaps                 Type / Size / Used / Priority

Verdicts (worst-first) :

  disk_swap_higher_than_zram   warn    A 'file' or 'partition'
                                       swap on a rotational/SSD
                                       device has priority >=
                                       a zram swap's priority.
                                       Defeats zram tiering.
  equal_priority_round_robin   accent  >=2 swaps share the same
                                       priority — round-robin
                                       blending; usually
                                       intentional but flag for
                                       review.
  single_swap_device           ok      Typical desktop layout.
  no_swap                      ok      No swap configured.
  unknown                              /proc/swaps absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "swap_priority_tiering_audit"

DEFAULT_PROC_SWAPS = "/proc/swaps"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_swaps(text: Optional[str]) -> list:
    """Return list of {filename, type, size, used, priority}."""
    out: list = []
    if not text:
        return out
    for i, line in enumerate(text.splitlines()):
        if i == 0:
            # Header: Filename Type Size Used Priority
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            out.append({
                "filename": parts[0],
                "type": parts[1],
                "size": int(parts[2]),
                "used": int(parts[3]),
                "priority": int(parts[4]),
            })
        except ValueError:
            continue
    return out


def is_zram(filename: str) -> bool:
    return "zram" in filename.lower()


def classify(swaps_present: bool,
             swaps: list) -> dict:
    if not swaps_present:
        return {"verdict": "unknown",
                "reason": "/proc/swaps absent."}
    if not swaps:
        return {"verdict": "ok",
                "reason": "No swap devices configured."}
    if len(swaps) == 1:
        return {"verdict": "ok",
                "reason": (
                    f"Single swap device "
                    f"({swaps[0]['filename']}, prio="
                    f"{swaps[0]['priority']}).")}

    zram_swaps = [s for s in swaps if is_zram(s["filename"])]
    disk_swaps = [s for s in swaps if not is_zram(s["filename"])]

    # warn — disk priority >= any zram priority
    if zram_swaps and disk_swaps:
        max_zram_prio = max(s["priority"] for s in zram_swaps)
        offenders = [s for s in disk_swaps
                     if s["priority"] >= max_zram_prio]
        if offenders:
            return {
                "verdict": "disk_swap_higher_than_zram",
                "reason": (
                    f"Disk swap(s) {[s['filename'] for s in offenders]} "
                    f"have priority >= max zram priority "
                    f"({max_zram_prio}). Cold pages bypass "
                    "zram and go straight to disk.")}

    # accent — multiple swaps share priority
    priorities = [s["priority"] for s in swaps]
    if len(set(priorities)) < len(priorities):
        return {
            "verdict": "equal_priority_round_robin",
            "reason": (
                f"{len(swaps)} swap devices share equal "
                "priority — round-robin blending. Verify "
                "this is intentional (typical for striped "
                "RAID-0 swap).")}

    return {"verdict": "ok",
            "reason": (
                f"{len(swaps)} swap device(s) with distinct "
                "priorities ; tier ordering coherent.")}


def status(config: Optional[dict] = None,
           proc_swaps: str = DEFAULT_PROC_SWAPS) -> dict:
    swaps_present = os.path.isfile(proc_swaps)
    swaps = (parse_swaps(_read_text(proc_swaps))
             if swaps_present else [])
    verdict = classify(swaps_present, swaps)
    return {
        "ok": verdict["verdict"] == "ok",
        "swap_count": len(swaps),
        "swaps": [
            {"filename": s["filename"],
             "type": s["type"],
             "priority": s["priority"]} for s in swaps],
        "verdict": verdict,
    }

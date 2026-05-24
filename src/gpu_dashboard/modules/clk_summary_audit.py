"""Module clk_summary_audit — Linux common-clock framework
state (R&D #83.2).

The /sys/kernel/debug/clk/clk_summary debug file lists every
clock tree node the kernel knows about :

  clock-name          enable_count  prepare_count  protect_count
                      rate  accuracy  phase  duty_cycle
                      hardware_enable  consumer  connection_id

A homelab user looking at idle wattage benefits from
detecting :

  * orphan clocks  ─ clocks left running with no consumer
                     (driver removed but clock never gated
                     off) ; classic idle-power leak.
  * unused clocks  ─ enabled but the consumer no longer
                     refers to them.
  * prepare/enable ─ the framework's two-stage gate is in a
    drift            half-on state ; the rare case where
                     prepare_count > enable_count by a wide
                     margin is suspicious.

debugfs is mode-700 on almost every distro, so for a user-
mode dashboard the dominant verdict is requires_root.

Verdicts (worst first) :

  orphan_clock_enabled   any clock with enable_count > 0 AND
                         consumer = "deviceless" /
                         "no_consumer".
  unused_clock_enabled   ≥ 3 top-level clocks have
                         enable_count > 0 with no
                         downstream consumer activity (drift
                         signal — wasted µW).
  prepare_enable_drift   any clock with prepare_count >
                         enable_count + 1 (stuck-prepared
                         state).
  ok                     no drift, tree consistent.
  requires_root          /sys/kernel/debug/clk unreadable.
  unknown                CONFIG_COMMON_CLK not built in —
                         no /sys/kernel/debug/clk dir.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_DEBUG_CLK = "/sys/kernel/debug/clk"

# Markers in the consumer column that indicate no real owner
_NO_CONSUMER_MARKERS = ("deviceless", "no_consumer")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_summary(text: str) -> list[dict]:
    """Returns list of {name, depth, enable, prepare, protect,
    consumer} parsed from clk_summary.  Header / separator
    lines are skipped."""
    out: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.lstrip().startswith("clock"):
            continue
        if line.lstrip().startswith("-"):
            continue
        if line.lstrip().startswith("enable"):
            continue
        # Determine depth by leading whitespace
        depth = len(line) - len(line.lstrip())
        toks = line.split()
        if len(toks) < 4:
            continue
        # Expected schema: name, enable, prepare, protect, ...
        name = toks[0]
        try:
            enable = int(toks[1])
            prepare = int(toks[2])
            protect = int(toks[3])
        except ValueError:
            continue
        # Consumer column is typically -3 (id column at end)
        consumer = toks[-2] if len(toks) >= 2 else ""
        out.append({
            "name": name,
            "depth": depth,
            "enable": enable,
            "prepare": prepare,
            "protect": protect,
            "consumer": consumer,
        })
    return out


def read_summary(root: str = DEFAULT_DEBUG_CLK
                  ) -> tuple[Optional[str], str]:
    """Returns (text, state) where state in
    {'ok', 'requires_root', 'unknown'}.

    Distinguishes :
      * unknown      = debugfs not mounted at all (parent
                       /sys/kernel/debug is absent).
      * requires_root = debugfs mounted but mode-700 on this
                       UID, so we can't tell whether clk_summary
                       is there or not.
      * ok           = clk_summary read successfully.
    """
    summary_path = os.path.join(root, "clk_summary")
    text = _read_text(summary_path)
    if text is not None:
        return (text, "ok")

    # Walk up from root to find a readable ancestor.
    parent = os.path.dirname(root) or "/"
    try:
        os.listdir(parent)
        parent_readable = True
    except (OSError, PermissionError):
        parent_readable = False
    if not parent_readable:
        # /sys/kernel/debug itself is mode-700 ; can't tell
        # whether clk is there → assume requires_root.
        return (None, "requires_root")
    # parent is readable ; if root dir is missing entirely
    # then this kernel has no CONFIG_COMMON_CLK.
    if not os.path.isdir(root):
        return (None, "unknown")
    # root exists but unreadable.
    return (None, "requires_root")


def classify(clocks: Optional[list[dict]],
             read_state: str) -> dict:
    if read_state == "unknown":
        return {"verdict": "unknown",
                "reason": (
                    "/sys/kernel/debug/clk absent — kernel "
                    "without CONFIG_COMMON_CLK / no debugfs "
                    "mount.")}
    if read_state == "requires_root":
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/kernel/debug/clk is mode-700. "
                    "Re-run dashboard as root for the clock "
                    "tree inventory.")}
    if not clocks:
        return {"verdict": "unknown",
                "reason": "clk_summary parsed empty."}

    # 1. err — orphan clocks (enable > 0 + no consumer)
    orphans = [
        c for c in clocks
        if c["enable"] > 0
        and c["consumer"] in _NO_CONSUMER_MARKERS]
    if orphans:
        first = orphans[0]
        return {"verdict": "orphan_clock_enabled",
                "reason": (
                    f"{len(orphans)} clock(s) enabled with no "
                    f"consumer (first: {first['name']}, "
                    f"enable={first['enable']}) — driver gone "
                    "but clock never gated off."),
                "orphan_count": len(orphans),
                "first_orphan": first["name"]}

    # 2. warn — top-level (depth 0) clocks enabled but no
    #    children depend on them (heuristic: depth-0 clocks
    #    with enable_count > 0 and no rows at depth > 0).
    #    For a rough drift signal we count top-level clocks
    #    with enable > 0 and no consumer-named owner.
    top_no_consumer = [
        c for c in clocks
        if c["depth"] == 0
        and c["enable"] > 0
        and c["consumer"] in _NO_CONSUMER_MARKERS]
    if len(top_no_consumer) >= 3:
        return {"verdict": "unused_clock_enabled",
                "reason": (
                    f"{len(top_no_consumer)} top-level "
                    "clocks enabled with no consumer — "
                    "wasted µW."),
                "top_clock_count": len(top_no_consumer)}

    # 3. accent — prepare/enable drift
    drift = [
        c for c in clocks
        if c["prepare"] > c["enable"] + 1]
    if drift:
        first = drift[0]
        return {"verdict": "prepare_enable_drift",
                "reason": (
                    f"{len(drift)} clock(s) have "
                    "prepare_count >> enable_count "
                    f"(first: {first['name']}, "
                    f"prepare={first['prepare']}, "
                    f"enable={first['enable']})."),
                "drift_count": len(drift),
                "first_drift": first["name"]}

    return {"verdict": "ok",
            "reason": (
                f"{len(clocks)} clock(s) audited ; tree "
                "consistent, no orphans, no prepare/enable "
                "drift.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_DEBUG_CLK) -> dict:
    text, read_state = read_summary(root)
    clocks: Optional[list[dict]] = (
        parse_summary(text) if text is not None else None)
    verdict = classify(clocks, read_state)
    return {
        "ok": verdict["verdict"] not in (
            "orphan_clock_enabled", "requires_root",
            "unknown"),
        "clock_count": len(clocks) if clocks else 0,
        "read_state": read_state,
        "verdict": verdict,
    }

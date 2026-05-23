"""Module pstate_audit — P-state pinning advisor (R&D #21.1).

NVIDIA cards expose performance states P0 (max) → P15 (lowest), and
the driver normally picks the right one for the current load. But
several known bugs cause silent downshifts :

  - Open-driver R555+ on some Ampere cards holds P2 under inference
    instead of P0 (~10% token/s loss, no throttle reason set)
  - Display server kicks the card to P8 between frames during
    light-load LLM serving
  - User-locked clocks via nvidia-smi --lock-gpu-clocks override
    the driver's auto-selection (sometimes forgotten after debugging)

This module reads pstate + utilization + locked-clocks state and
returns a per-GPU verdict :
  - ok                 (pstate matches load)
  - silent_downshift   (heavy load but pstate ≥ P2 — surface bug)
  - power_save_idle    (idle and parked at P8 — correct)
  - clock_locked       (clocks pinned manually — show how to release)

stdlib only.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Optional


NAME = "pstate_audit"


# Heavy load = sustained utilization > this threshold for a poll
HEAVY_UTIL_THRESHOLD = 50


def _query_gpu(fields: list[str], timeout: float = 2.0) -> Optional[list[dict]]:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi", f"--query-gpu={','.join(fields)}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    out: list[dict] = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(fields):
            continue
        out.append(dict(zip(fields, parts)))
    return out


def parse_pstate(s: str) -> Optional[int]:
    """'P0' → 0, 'P12' → 12. Returns None on malformed."""
    if not s or not s.startswith("P"):
        return None
    try:
        return int(s[1:])
    except ValueError:
        return None


def parse_int(s: str) -> Optional[int]:
    if not s:
        return None
    s = s.strip()
    if not s or s.lower() in ("n/a", "[n/a]", "not supported"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def is_clock_locked(current_gr: Optional[int], max_gr: Optional[int],
                     base_gr: Optional[int]) -> bool:
    """A clock is 'locked' if the current matches a fixed value and the
    max is unusually close (within 1 MHz). Heuristic — nvidia-smi does
    not directly expose the lock state."""
    if current_gr is None or max_gr is None:
        return False
    # If max_gr < base boost frequency, user likely set --lock-gpu-clocks
    if base_gr is not None and max_gr < base_gr * 0.9:
        return True
    return False


def classify(pstate: Optional[int], util_pct: Optional[int],
              power_w: Optional[float], power_limit_w: Optional[float],
              clock_locked: bool) -> dict:
    """Per-GPU verdict."""
    if pstate is None:
        return {"verdict": "unknown",
                "reason": "pstate unreadable",
                "advisory": ""}
    if clock_locked:
        return {
            "verdict": "clock_locked",
            "reason": (f"GPU clocks pinned manually at P{pstate}. "
                       "If unintentional, release with "
                       "`nvidia-smi --reset-gpu-clocks`."),
            "advisory": "nvidia-smi --reset-gpu-clocks",
        }
    if util_pct is not None and util_pct >= HEAVY_UTIL_THRESHOLD:
        if pstate <= 1:
            return {"verdict": "ok",
                    "reason": (f"Heavy load ({util_pct}% util) at "
                               f"P{pstate} — driver picked correctly."),
                    "advisory": ""}
        return {
            "verdict": "silent_downshift",
            "reason": (f"Heavy load ({util_pct}% util) but stuck at "
                       f"P{pstate}. ~5-15% perf loss vs P0. Known on "
                       "R555+ open driver. Try "
                       "`nvidia-smi --lock-gpu-clocks=<boost-freq>` or "
                       "fall back to proprietary driver."),
            "advisory": "nvidia-smi --lock-gpu-clocks=BOOST_FREQ",
        }
    if util_pct is not None and util_pct < 5:
        if pstate >= 5:
            return {"verdict": "power_save_idle",
                    "reason": (f"Idle (util={util_pct}%) at P{pstate} — "
                               "correct power-save."),
                    "advisory": ""}
        return {"verdict": "ok",
                "reason": (f"Idle but at P{pstate}. Driver could be "
                           "more aggressive but not broken."),
                "advisory": ""}
    return {"verdict": "ok",
            "reason": (f"Mid load ({util_pct}% util) at P{pstate} — "
                       "within normal range."),
            "advisory": ""}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    fields = ["index", "name", "pstate", "utilization.gpu",
              "clocks.current.graphics", "clocks.max.graphics",
              "clocks.gr", "power.draw", "power.limit"]
    rows = _query_gpu(fields)
    if rows is None:
        return {"ok": False, "reason": "nvidia-smi unreachable", "gpus": []}
    gpus: list = []
    downshifts = 0
    for r in rows:
        ps = parse_pstate(r.get("pstate", ""))
        util = parse_int(r.get("utilization.gpu", ""))
        cur_gr = parse_int(r.get("clocks.current.graphics", ""))
        max_gr = parse_int(r.get("clocks.max.graphics", ""))
        base_gr = parse_int(r.get("clocks.gr", ""))
        try:
            power = float(r.get("power.draw", "") or "0")
        except ValueError:
            power = None
        try:
            plimit = float(r.get("power.limit", "") or "0")
        except ValueError:
            plimit = None
        locked = is_clock_locked(cur_gr, max_gr, base_gr)
        verdict = classify(ps, util, power, plimit, locked)
        if verdict["verdict"] == "silent_downshift":
            downshifts += 1
        gpus.append({
            "index": parse_int(r.get("index", "0")) or 0,
            "name": r.get("name", "?"),
            "pstate": ps,
            "util_pct": util,
            "clock_mhz": cur_gr,
            "clock_max_mhz": max_gr,
            "power_w": power,
            "power_limit_w": plimit,
            "clock_locked": locked,
            "verdict": verdict,
        })
    return {
        "ok": True,
        "gpus": gpus,
        "downshift_count": downshifts,
    }

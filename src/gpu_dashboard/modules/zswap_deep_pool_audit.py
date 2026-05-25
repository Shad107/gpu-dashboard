"""Module zswap_deep_pool_audit — zswap newer-knob + debugfs
runtime counter posture (R&D #100.4).

The existing zswap_zram_audit checks the basic on/off + compressor
+ zpool + max_pool_percent. It does NOT touch the newer kernel
parameters that determine actual compression efficiency :

  /sys/module/zswap/parameters/
    same_filled_pages_enabled       # store zero-pages as ref
    non_same_filled_pages_enabled   # store all compressible
    shrinker_enabled                # let MM reclaim pool
    exclusive_loads                 # drop dup compressed page

  /sys/kernel/debug/zswap/
    pool_total_size
    stored_pages
    pool_limit_hit                  # rejected ; direct-swapped
    reject_compress_poor            # too-large-to-compress

On RAM-limited desktops (32 GB rigs running 13B-param models),
a misconfigured exclusive_loads=N doubles RAM pressure
invisibly. Params may or may not be present depending on
kernel version — we degrade gracefully.

Verdicts (worst-first) :

  zswap_pool_limit_hit_persistent  err     pool_limit_hit
                                           non-zero AND
                                           reject_compress_poor
                                           rising — pool
                                           rejecting, system
                                           direct-swapping.
  zswap_exclusive_loads_off        warn    exclusive_loads=N
                                           — duplicate
                                           compressed copy
                                           kept after fault.
  zswap_shrinker_disabled          accent  shrinker_enabled=N
                                           despite zswap on.
  ok                                       zswap off or all
                                           knobs sane.
  requires_root                            debugfs zswap
                                           unreadable.
  unknown                                  /sys/module/zswap
                                           absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "zswap_deep_pool_audit"

DEFAULT_PARAMS = "/sys/module/zswap/parameters"
DEFAULT_DEBUGFS = "/sys/kernel/debug/zswap"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_str(path: str) -> Optional[str]:
    t = _read_text(path)
    return t.strip() if t is not None else None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def _is_y(s: Optional[str]) -> bool:
    return s is not None and s.upper() in ("Y", "1", "TRUE")


def classify(params_present: bool,
             enabled: Optional[str],
             exclusive_loads: Optional[str],
             shrinker_enabled: Optional[str],
             pool_limit_hit: Optional[int],
             reject_compress_poor: Optional[int],
             debugfs_unreadable: bool) -> dict:
    if not params_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/module/zswap absent — kernel "
                    "built without zswap or module not "
                    "loaded.")}

    # If zswap is off, nothing else matters
    if not _is_y(enabled):
        return {"verdict": "ok",
                "reason": (
                    f"zswap.enabled={enabled} — disabled, "
                    "nothing to audit.")}

    # err — pool_limit_hit + reject_compress_poor both nonzero
    if (pool_limit_hit is not None
            and pool_limit_hit > 0
            and reject_compress_poor is not None
            and reject_compress_poor > 0):
        return {
            "verdict": "zswap_pool_limit_hit_persistent",
            "reason": (
                f"zswap pool_limit_hit={pool_limit_hit} + "
                f"reject_compress_poor="
                f"{reject_compress_poor} — pool full, "
                "kernel is direct-swapping uncompressed "
                "pages. Bump max_pool_percent or compress "
                "ratio.")}

    # If debugfs unreadable, downgrade to requires_root only
    # when we cannot determine other state
    if (debugfs_unreadable
            and pool_limit_hit is None
            and exclusive_loads is None
            and shrinker_enabled is None):
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/kernel/debug/zswap unreadable "
                    "and no module params expose state — "
                    "re-run as root.")}

    # warn — exclusive_loads off
    if (exclusive_loads is not None
            and not _is_y(exclusive_loads)):
        return {
            "verdict": "zswap_exclusive_loads_off",
            "reason": (
                f"zswap.exclusive_loads={exclusive_loads} "
                "— duplicate compressed copy retained "
                "after fault-in. Doubles effective RAM "
                "pressure.")}

    # accent — shrinker disabled
    if (shrinker_enabled is not None
            and not _is_y(shrinker_enabled)):
        return {
            "verdict": "zswap_shrinker_disabled",
            "reason": (
                f"zswap.shrinker_enabled={shrinker_enabled}"
                " despite zswap on — MM cannot reclaim the "
                "pool under pressure.")}

    return {"verdict": "ok",
            "reason": (
                f"zswap.enabled={enabled} ; "
                f"exclusive_loads={exclusive_loads} ; "
                f"shrinker_enabled={shrinker_enabled} ; "
                f"pool_limit_hit={pool_limit_hit}. Sane.")}


def status(config: Optional[dict] = None,
           params: str = DEFAULT_PARAMS,
           debugfs: str = DEFAULT_DEBUGFS) -> dict:
    params_present = os.path.isdir(params)
    enabled = (_read_str(os.path.join(params, "enabled"))
               if params_present else None)
    exclusive_loads = (
        _read_str(os.path.join(params, "exclusive_loads"))
        if params_present else None)
    shrinker_enabled = (
        _read_str(os.path.join(params, "shrinker_enabled"))
        if params_present else None)
    pool_limit_hit = _read_int(
        os.path.join(debugfs, "pool_limit_hit"))
    reject_compress_poor = _read_int(
        os.path.join(debugfs, "reject_compress_poor"))
    debugfs_unreadable = (
        os.path.isdir(debugfs)
        and not os.access(debugfs, os.R_OK))

    verdict = classify(
        params_present, enabled, exclusive_loads,
        shrinker_enabled, pool_limit_hit,
        reject_compress_poor, debugfs_unreadable)
    return {
        "ok": verdict["verdict"] == "ok",
        "enabled": enabled,
        "exclusive_loads": exclusive_loads,
        "shrinker_enabled": shrinker_enabled,
        "pool_limit_hit": pool_limit_hit,
        "reject_compress_poor": reject_compress_poor,
        "verdict": verdict,
    }

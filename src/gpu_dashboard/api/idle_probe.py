"""HTTP handler for /idle.txt + /idle.json — one-liner idle probe (R&D #17.7).

Designed to be grep'd by shell scripts, tmux statuslines, Conky widgets,
ssh motd, or remote monitors that just want a quick "is the rig
actually idle right now ?" answer in one curl.

Response format (one line, ~80 chars max) :
  IDLE 95% gpu0=12W since=14m
or
  ACTIVE 23% gpu0=180W util=87 vram=12.5/24GiB

stdlib only.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

from . import _core as _m


Response = Tuple[int, str]


def _gpus_available(*args, **kw):
    return _m._gpus_available(*args, **kw)


def _gpu_card_snapshot(*args, **kw):
    return _m._gpu_card_snapshot(*args, **kw)


_IDLE_UTIL_PCT = 15      # below this %, considered idle
_IDLE_POWER_W = 30       # below this W, considered idle


def _classify(snap: dict, util_thresh: float, power_thresh: float) -> bool:
    """True if this GPU sample looks idle."""
    if not snap or not snap.get("alive"):
        return True  # offline = idle by definition
    util = float(snap.get("util_gpu") or 0)
    power = float(snap.get("power") or 0)
    return util < util_thresh and power < power_thresh


def _format_duration(seconds: int) -> str:
    if seconds < 60: return f"{seconds}s"
    if seconds < 3600: return f"{seconds // 60}m"
    if seconds < 86400: return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _idle_since_seconds(ctx: dict, current_idle: bool,
                        util_thresh: float, power_thresh: float) -> Optional[int]:
    """Walk recent sampler buffer backwards to find the last non-idle
    sample. Returns the number of seconds since then."""
    sampler = ctx.get("sampler")
    if not sampler:
        return None
    try:
        snaps = sampler.snapshot()
    except Exception:
        return None
    if not snaps:
        return None
    # Walk newest-to-oldest. snap['ts'] may be int or 'HH:MM:SS' string.
    now = time.time()
    last_non_idle_ts: Optional[float] = None
    for s in reversed(snaps):
        ts_raw = s.get("ts")
        try:
            ts = float(ts_raw)
        except (ValueError, TypeError):
            continue
        if not _classify(s, util_thresh, power_thresh):
            last_non_idle_ts = ts
            break
    if last_non_idle_ts is None:
        # All samples in the buffer are idle — return age of oldest sample
        try:
            oldest = float(snaps[0].get("ts", now))
            return int(max(0, now - oldest))
        except (ValueError, TypeError):
            return None
    return int(max(0, now - last_non_idle_ts))


def _gather(ctx: dict, util_thresh: float, power_thresh: float) -> dict:
    """Read live + idle-since. Returns the data dict used by both
    text and JSON renderers."""
    try:
        gpus = _gpus_available() or []
    except Exception:
        gpus = []
    snaps: list = []
    for g in gpus:
        try:
            idx = int(g.get("index", g.get("idx", 0)))
        except (ValueError, TypeError):
            continue
        snaps.append({"index": idx, **(_gpu_card_snapshot(gpu_index=idx) or {})})
    if not snaps:
        snaps = [{"index": 0, "alive": False}]
    overall_idle = all(_classify(s, util_thresh, power_thresh) for s in snaps)
    since_s = _idle_since_seconds(ctx, overall_idle, util_thresh, power_thresh)
    return {
        "idle": overall_idle,
        "since_s": since_s,
        "util_thresh": util_thresh,
        "power_thresh": power_thresh,
        "gpus": snaps,
    }


def handle_idle_txt(ctx: dict, params: Optional[dict] = None) -> Response:
    """Return a one-liner string."""
    params = params or {}
    try:
        ut = float(params.get("util_thresh", _IDLE_UTIL_PCT))
        pt = float(params.get("power_thresh", _IDLE_POWER_W))
    except (ValueError, TypeError):
        ut, pt = _IDLE_UTIL_PCT, _IDLE_POWER_W
    data = _gather(ctx, util_thresh=ut, power_thresh=pt)

    state = "IDLE" if data["idle"] else "ACTIVE"
    # Per-GPU compact suffix
    gpu_parts = []
    util_max = 0.0
    for s in data["gpus"]:
        if not s.get("alive"):
            gpu_parts.append(f"gpu{s['index']}=off")
            continue
        u = float(s.get("util_gpu") or 0)
        p = float(s.get("power") or 0)
        util_max = max(util_max, u)
        gpu_parts.append(f"gpu{s['index']}={p:.0f}W")
    util_pct_str = f"{util_max:.0f}%"
    line = f"{state} {util_pct_str} " + " ".join(gpu_parts)
    if data["since_s"] is not None:
        line += f" since={_format_duration(data['since_s'])}"
    return 200, line + "\n"


def handle_idle_json(ctx: dict, params: Optional[dict] = None) -> Tuple[int, dict]:
    """Structured response for consumers."""
    params = params or {}
    try:
        ut = float(params.get("util_thresh", _IDLE_UTIL_PCT))
        pt = float(params.get("power_thresh", _IDLE_POWER_W))
    except (ValueError, TypeError):
        ut, pt = _IDLE_UTIL_PCT, _IDLE_POWER_W
    data = _gather(ctx, util_thresh=ut, power_thresh=pt)
    return 200, {"ok": True, **data}

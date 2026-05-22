"""Module inference_cost — cost-per-prompt + tok/Wh efficiency (R&D #14.4).

Computes the marginal energy cost of LLM inference over rolling windows by
joining :
  - LLM token deltas (from the existing SQLite samples table)
  - GPU power × time → kWh
  - Optionally a wall-meter reading (Shelly/Tasmota) for true PSU+system Wh

Output : {window_s, tokens_delta, kwh_gpu, cost_gpu_eur, tok_per_wh_gpu,
          cost_per_1k_tokens_eur, ...}

If a wall-meter is configured (R&D #12.1), also computes the wall-side
numbers (which include CPU + chipset + fans).

Pure functions — easy to unit-test by feeding sample lists.

stdlib only.
"""
from __future__ import annotations

import time
from typing import Optional


NAME = "inference_cost"


def _integrate_kwh(samples: list, value_key: str = "power",
                   tokens_key: str = "tokens_total_snapshot",
                   default_dt_s: float = 5.0) -> dict:
    """Compute energy + token deltas from a list of sorted samples.

    Each sample is a dict with at least `ts` (seconds) and either `power`
    or the requested value_key. Tokens are counted only when the counter
    monotonically increases (negative deltas = llama-server restart).

    Returns : {window_s, tokens_delta, kwh, avg_watts, restart_count}.
    """
    if not samples or len(samples) < 2:
        return {"window_s": 0, "tokens_delta": 0, "kwh": 0.0,
                "avg_watts": 0.0, "restart_count": 0, "sample_count": len(samples)}
    # Sort by ts ascending
    s = sorted(samples, key=lambda x: x.get("ts", 0))
    first_ts = float(s[0].get("ts", 0))
    last_ts = float(s[-1].get("ts", 0))
    window_s = max(default_dt_s, last_ts - first_ts)

    # Trapezoidal integration of power × Δt
    total_wh = 0.0
    n_p = 0
    sum_p = 0.0
    for i in range(1, len(s)):
        p0 = s[i - 1].get(value_key)
        p1 = s[i].get(value_key)
        if p0 is None or p1 is None:
            continue
        try:
            p0f, p1f = float(p0), float(p1)
        except (ValueError, TypeError):
            continue
        try:
            dt = float(s[i].get("ts", 0)) - float(s[i - 1].get("ts", 0))
        except (ValueError, TypeError):
            dt = default_dt_s
        if dt <= 0 or dt > 3600:  # protect against bad timestamps
            dt = default_dt_s
        avg_p = (p0f + p1f) / 2
        total_wh += avg_p * dt / 3600
        sum_p += avg_p
        n_p += 1

    # Token delta (sum of positive deltas only)
    token_delta = 0
    prev_tok = None
    restarts = 0
    for x in s:
        tok = x.get(tokens_key)
        if tok is None:
            continue
        try:
            cur = int(tok)
        except (ValueError, TypeError):
            continue
        if prev_tok is not None:
            d = cur - prev_tok
            if d > 0:
                token_delta += d
            elif d < 0:
                restarts += 1
        prev_tok = cur

    avg_watts = (sum_p / n_p) if n_p else 0.0
    return {
        "window_s": int(window_s),
        "tokens_delta": int(token_delta),
        "kwh": round(total_wh / 1000, 6),
        "avg_watts": round(avg_watts, 1),
        "restart_count": restarts,
        "sample_count": len(s),
    }


def compute_costs(integrated: dict, price_eur_per_kwh: float,
                  wall_kwh: Optional[float] = None,
                  wall_baseline_w: Optional[float] = None) -> dict:
    """Apply price + optional wall-side numbers."""
    out = dict(integrated)
    out["price_eur_per_kwh"] = price_eur_per_kwh
    out["cost_gpu_eur"] = round(integrated["kwh"] * price_eur_per_kwh, 6)
    if integrated["kwh"] > 0 and integrated["tokens_delta"] > 0:
        tok_per_wh = integrated["tokens_delta"] / (integrated["kwh"] * 1000)
        out["tok_per_wh_gpu"] = round(tok_per_wh, 2)
        out["cost_per_1k_tokens_eur"] = round(
            out["cost_gpu_eur"] / integrated["tokens_delta"] * 1000, 6
        )
    else:
        out["tok_per_wh_gpu"] = None
        out["cost_per_1k_tokens_eur"] = None

    # Wall-side : if a wall-meter delta is available, compute the same metrics
    # at the wall socket. wall_kwh should already represent the same window.
    if wall_kwh is not None and wall_kwh > 0:
        out["kwh_wall"] = round(wall_kwh, 6)
        out["cost_wall_eur"] = round(wall_kwh * price_eur_per_kwh, 6)
        if integrated["tokens_delta"] > 0:
            out["tok_per_wh_wall"] = round(
                integrated["tokens_delta"] / (wall_kwh * 1000), 2
            )
            out["cost_per_1k_tokens_wall_eur"] = round(
                out["cost_wall_eur"] / integrated["tokens_delta"] * 1000, 6
            )
            # PSU/system overhead = (kwh_wall - kwh_gpu) / kwh_wall × 100
            if wall_kwh > integrated["kwh"]:
                out["overhead_pct"] = round(
                    (wall_kwh - integrated["kwh"]) / wall_kwh * 100, 1
                )
    return out


def status(storage, cfg, windows: Optional[list] = None) -> dict:
    """Top-level snapshot computed over several rolling windows.

    Returns per-window {window_s, tokens, kwh_gpu, cost_gpu_eur, tok_per_wh_gpu,
    cost_per_1k_tokens_eur, [wall-side metrics if available]}.
    """
    if windows is None:
        windows = [60, 600, 3600, 86400]  # 1m, 10m, 1h, 24h
    try:
        price = float(cfg.get("ELECTRICITY_PRICE_EUR_PER_KWH", "0.25") or "0.25")
    except (ValueError, TypeError):
        price = 0.25
    now = int(time.time())
    out: dict = {
        "ok": True,
        "price_eur_per_kwh": price,
        "windows": {},
    }
    if storage is None:
        return {**out, "available": False, "reason": "storage unavailable"}
    for w in windows:
        from_ts = now - int(w)
        try:
            samples = storage.get_samples(from_ts=from_ts, to_ts=now, gpu_index=0)
        except Exception:
            samples = []
        integrated = _integrate_kwh(samples)
        cost = compute_costs(integrated, price_eur_per_kwh=price)
        out["windows"][str(w)] = cost
    # Whole-server tok/Wh — headline metric for the badge
    best = out["windows"].get("3600", {})
    out["headline_tok_per_wh"] = best.get("tok_per_wh_gpu")
    out["available"] = True
    return out

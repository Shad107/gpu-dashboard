"""Module tariff — tariff-aware job scheduling (R&D #15.2).

Many electricity contracts have time-of-use pricing : cheap nights / weekends,
expensive 17h-21h peaks. This module reads a user-supplied CSV of hourly
€/kWh rates and helps users :

  - See the current rate at a glance + day's peak/off-peak windows
  - Estimate a job's cost given expected watts + duration
  - Find the cheapest start time within the next 24h for a given job duration

CSV format at ~/.config/gpu-dashboard/tariffs.csv :
  # comments + blank lines ok
  hour_of_day,eur_per_kwh
  0,0.13
  1,0.13
  ...
  17,0.27
  18,0.27
  19,0.27
  ...
  23,0.18

24 hourly entries are typical ; missing hours fall back to the day's average.

stdlib only.
"""
from __future__ import annotations

import csv
import datetime
import os
import threading
import time
from typing import Optional


NAME = "tariff"

_CSV_PATH = "~/.config/gpu-dashboard/tariffs.csv"
_CACHE_TTL_S = 300  # reload every 5 min

_cache: dict = {"by_hour": None, "loaded_ts": 0}
_lock = threading.Lock()


def csv_path() -> str:
    return os.path.expanduser(_CSV_PATH)


def load_csv(force: bool = False) -> dict:
    """Return {hour: €/kWh}. In-process cache, 5-min TTL."""
    path = csv_path()
    with _lock:
        now = int(time.time())
        if (not force and _cache["by_hour"] is not None
                and (now - _cache["loaded_ts"]) < _CACHE_TTL_S):
            return _cache["by_hour"]
        out: dict = {}
        if not os.path.exists(path):
            _cache["by_hour"] = out
            _cache["loaded_ts"] = now
            return out
        try:
            with open(path) as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row or row[0].lstrip().startswith("#"):
                        continue
                    if len(row) < 2:
                        continue
                    try:
                        hour = int(row[0].strip())
                        price = float(row[1].strip())
                    except ValueError:
                        continue
                    if 0 <= hour <= 23 and price >= 0:
                        out[hour] = price
        except OSError:
            pass
        _cache["by_hour"] = out
        _cache["loaded_ts"] = now
        return out


def current_rate(now_hour: Optional[int] = None) -> Optional[float]:
    by_hour = load_csv()
    if not by_hour:
        return None
    if now_hour is None:
        now_hour = datetime.datetime.now().hour
    if now_hour in by_hour:
        return by_hour[now_hour]
    return sum(by_hour.values()) / len(by_hour)


def estimate_job_cost(watts_avg: float, duration_s: float,
                      start_hour: Optional[int] = None) -> Optional[dict]:
    """Compute the expected €-cost of a job starting at `start_hour` (or now)
    drawing `watts_avg` for `duration_s`. Crosses hour boundaries.

    Returns {kwh, cost_eur, hours_breakdown: [{hour, share_s, rate, cost}]}
    or None if no tariff file."""
    by_hour = load_csv()
    if not by_hour:
        return None
    if start_hour is None:
        start_hour = datetime.datetime.now().hour
    avg_rate = sum(by_hour.values()) / len(by_hour) if by_hour else 0.25
    # Walk forward across hour boundaries
    remaining = float(duration_s)
    cost = 0.0
    kwh = 0.0
    seconds_into_hour = (datetime.datetime.now().minute * 60 +
                         datetime.datetime.now().second
                         if start_hour == datetime.datetime.now().hour else 0)
    cur_hour = start_hour
    breakdown: list = []
    while remaining > 0:
        # Slice this hour
        seconds_left_in_hour = 3600 - seconds_into_hour
        slice_s = min(remaining, seconds_left_in_hour)
        rate = by_hour.get(cur_hour, avg_rate)
        slice_kwh = watts_avg * slice_s / 3600 / 1000
        slice_cost = slice_kwh * rate
        breakdown.append({
            "hour": cur_hour, "share_s": int(slice_s),
            "rate_eur_per_kwh": round(rate, 4),
            "kwh": round(slice_kwh, 6),
            "cost_eur": round(slice_cost, 6),
        })
        kwh += slice_kwh
        cost += slice_cost
        remaining -= slice_s
        seconds_into_hour = 0
        cur_hour = (cur_hour + 1) % 24
    return {
        "watts_avg": watts_avg,
        "duration_s": int(duration_s),
        "start_hour": start_hour,
        "kwh": round(kwh, 6),
        "cost_eur": round(cost, 6),
        "hours_breakdown": breakdown,
    }


def find_cheapest_start(watts_avg: float, duration_s: float,
                        within_h: int = 24) -> Optional[dict]:
    """For a job of given watts × duration, find the start hour
    in the next `within_h` hours with the lowest total cost."""
    by_hour = load_csv()
    if not by_hour:
        return None
    candidates: list = []
    now_hour = datetime.datetime.now().hour
    for offset in range(within_h):
        h = (now_hour + offset) % 24
        est = estimate_job_cost(watts_avg, duration_s, start_hour=h)
        if est is None:
            continue
        candidates.append({
            "start_hour": h,
            "hours_until_start": offset,
            "cost_eur": est["cost_eur"],
            "kwh": est["kwh"],
        })
    if not candidates:
        return None
    candidates.sort(key=lambda c: c["cost_eur"])
    best = candidates[0]
    worst = candidates[-1]
    savings = round(worst["cost_eur"] - best["cost_eur"], 4)
    return {
        "best": best,
        "worst_for_comparison": worst,
        "absolute_savings_eur": savings,
        "savings_pct": round(savings / worst["cost_eur"] * 100, 1)
                       if worst["cost_eur"] > 0 else 0,
        "candidates": candidates,
    }


def status() -> dict:
    by_hour = load_csv()
    if not by_hour:
        return {
            "ok": True, "available": False,
            "reason": f"no tariffs CSV at {csv_path()} — add one to enable",
            "csv_path": csv_path(),
        }
    rate = current_rate()
    now_hour = datetime.datetime.now().hour
    out = {
        "ok": True, "available": True,
        "csv_path": csv_path(),
        "current_hour": now_hour,
        "current_eur_per_kwh": round(rate, 4) if rate is not None else None,
        "day_min_eur_per_kwh": round(min(by_hour.values()), 4),
        "day_max_eur_per_kwh": round(max(by_hour.values()), 4),
        "day_avg_eur_per_kwh": round(sum(by_hour.values()) / len(by_hour), 4),
        "hours_loaded": len(by_hour),
    }
    # Identify the cheapest + most expensive hours of the day for the chart
    sorted_hours = sorted(by_hour.items(), key=lambda kv: kv[1])
    out["cheapest_hours"] = [h for h, _ in sorted_hours[:6]]
    out["peak_hours"] = [h for h, _ in sorted_hours[-6:]]
    return out

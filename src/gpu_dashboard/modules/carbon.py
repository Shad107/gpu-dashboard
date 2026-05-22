"""Module carbon — grid carbon intensity overlay (R&D #13.4).

Multiplies live power draw by user-supplied grid carbon intensity to
display gCO2/token (and gCO2 today / month).

Approach :
  - User drops a CSV at ~/.config/gpu-dashboard/grid_intensity.csv :
      hour_of_day,gco2_per_kwh
      0,45
      1,42
      ...
      23,68
  - Optional second column 'mode' (avg | marginal) for advanced users.
  - We load + cache once, then look up the current hour for live calcs.
  - Default to a flat 'unknown' if no CSV exists (feature gracefully
    disabled, NOT an error).

Air-gap compatible : everything is local, no outbound fetch.

Stdlib only.
"""
from __future__ import annotations

import csv
import datetime
import os
import threading
from typing import Optional


NAME = "carbon"

_CSV_PATH = "~/.config/gpu-dashboard/grid_intensity.csv"

_cache: dict = {"by_hour": None, "loaded_ts": 0}
_cache_lock = threading.Lock()
_CACHE_TTL_S = 300  # reload every 5 min


def csv_path() -> str:
    return os.path.expanduser(_CSV_PATH)


def load_csv(force: bool = False) -> dict:
    """Read the CSV and return {hour: gco2_per_kwh}. Cached in-process.
    Returns empty dict if file missing or malformed."""
    import time as _time
    path = csv_path()
    with _cache_lock:
        now = int(_time.time())
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
                        gco2 = float(row[1].strip())
                    except ValueError:
                        continue
                    if 0 <= hour <= 23 and gco2 >= 0:
                        out[hour] = gco2
        except OSError:
            pass
        _cache["by_hour"] = out
        _cache["loaded_ts"] = now
        return out


def current_intensity(now_hour: Optional[int] = None) -> Optional[float]:
    """Return gCO2/kWh for the current (or given) hour. None if no CSV."""
    by_hour = load_csv()
    if not by_hour:
        return None
    if now_hour is None:
        now_hour = datetime.datetime.now().hour
    if now_hour in by_hour:
        return by_hour[now_hour]
    # Fallback : daily average of whatever we have
    return sum(by_hour.values()) / len(by_hour)


def gco2_per_token(watts: float, tokens_per_second: float,
                   gco2_per_kwh: float) -> Optional[float]:
    """gCO2 emitted per token at this (watts, tok/s) operating point.

    watts × Wh-per-s × kWh-conv × gCO2/kWh / tokens-per-second
    = watts × (1/3600) × (1/1000) × gco2 / tps
    = watts × gco2 / (tps × 3.6e6)
    """
    if tokens_per_second <= 0 or watts < 0 or gco2_per_kwh < 0:
        return None
    return (watts * gco2_per_kwh) / (tokens_per_second * 3_600_000)


def gco2_for_kwh(kwh: float, gco2_per_kwh: float) -> float:
    """Total grams of CO2 emitted by a given kWh consumption."""
    return max(0.0, kwh * gco2_per_kwh)


def status(cfg=None,
           kwh_today: Optional[float] = None,
           kwh_month: Optional[float] = None,
           watts_now: Optional[float] = None,
           tps_now: Optional[float] = None) -> dict:
    """Top-level snapshot for the UI / API."""
    intensity = current_intensity()
    if intensity is None:
        return {
            "ok": True,
            "available": False,
            "reason": f"no grid intensity CSV at {csv_path()} — add one to enable carbon view",
            "csv_path": csv_path(),
        }
    out: dict = {
        "ok": True,
        "available": True,
        "current_gco2_per_kwh": round(intensity, 1),
        "current_hour": datetime.datetime.now().hour,
        "csv_path": csv_path(),
    }
    if kwh_today is not None:
        out["gco2_today_g"] = round(gco2_for_kwh(kwh_today, intensity), 1)
    if kwh_month is not None:
        out["gco2_month_kg"] = round(gco2_for_kwh(kwh_month, intensity) / 1000, 2)
    if watts_now is not None and tps_now is not None and tps_now > 0:
        per_token = gco2_per_token(watts_now, tps_now, intensity)
        if per_token is not None:
            out["gco2_per_token_g"] = round(per_token, 6)
    # Helpful aggregates : day's min/max intensity
    by_hour = load_csv()
    if by_hour:
        out["day_min_gco2_per_kwh"] = round(min(by_hour.values()), 1)
        out["day_max_gco2_per_kwh"] = round(max(by_hour.values()), 1)
        out["day_avg_gco2_per_kwh"] = round(sum(by_hour.values()) / len(by_hour), 1)
    return out

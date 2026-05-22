"""Scheduled benchmark runs — let users wire a weekly drift check.

Use case : a user wants the dashboard to auto-run a sweet-vs-boost benchmark
every Sunday at 03:00 to verify their cooling/perf hasn't drifted week-over-week.

This module is pure-Python helpers — no threads, no I/O orchestration. The
auto_profile daemon (or a future scheduler daemon) calls `due_entries()`
each tick and runs them via `benchmark.run_segment + compare()`.

Schedule grammar — kept minimal :
  'daily_HH'           e.g. 'daily_03' = every day at 03:00
  'weekly_DDD_HH'      e.g. 'weekly_sun_03' = Sundays at 03:00 (DDD ∈ mon..sun)
  'interval_HOURS'     e.g. 'interval_24' = every 24 hours since last_run_ts

Anything else is rejected by `validate_schedule()`.
"""
from __future__ import annotations

import datetime
import json
import os
import re
from typing import Optional


NAME = "benchmark_scheduler"


_DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def validate_schedule(spec: str) -> bool:
    """Returns True if `spec` matches one of the supported schedule grammars."""
    if not isinstance(spec, str):
        return False
    if re.match(r"^daily_([01]?\d|2[0-3])$", spec):
        return True
    if re.match(r"^weekly_(mon|tue|wed|thu|fri|sat|sun)_([01]?\d|2[0-3])$", spec):
        return True
    if re.match(r"^interval_(\d{1,4})$", spec):
        return True
    return False


def _parse_daily_hour(spec: str) -> Optional[int]:
    m = re.match(r"^daily_(\d{1,2})$", spec)
    return int(m.group(1)) if m else None


def _parse_weekly(spec: str) -> Optional[tuple]:
    m = re.match(r"^weekly_(mon|tue|wed|thu|fri|sat|sun)_(\d{1,2})$", spec)
    if not m:
        return None
    return _DAY_NAMES.index(m.group(1)), int(m.group(2))


def _parse_interval(spec: str) -> Optional[int]:
    """Returns hours, or None if not an interval spec."""
    m = re.match(r"^interval_(\d{1,4})$", spec)
    return int(m.group(1)) if m else None


def next_run_ts(spec: str, last_run_ts: int = 0, now: Optional[int] = None) -> int:
    """Compute the next epoch timestamp when this schedule fires.

    For interval_HH : last_run_ts + H*3600. (For first run, last_run_ts=0,
    so returned ts is 0 → should_run_now returns True immediately.)

    For daily/weekly : next absolute wallclock match after `now`.
    """
    if now is None:
        import time as _t
        now = int(_t.time())

    hour = _parse_daily_hour(spec)
    if hour is not None:
        cur = datetime.datetime.fromtimestamp(now)
        candidate = cur.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate.timestamp() <= now:
            candidate += datetime.timedelta(days=1)
        return int(candidate.timestamp())

    wk = _parse_weekly(spec)
    if wk is not None:
        target_weekday, hour = wk
        cur = datetime.datetime.fromtimestamp(now)
        candidate = cur.replace(hour=hour, minute=0, second=0, microsecond=0)
        # Adjust to target weekday
        days_ahead = (target_weekday - cur.weekday()) % 7
        candidate += datetime.timedelta(days=days_ahead)
        if candidate.timestamp() <= now:
            candidate += datetime.timedelta(days=7)
        return int(candidate.timestamp())

    interval_h = _parse_interval(spec)
    if interval_h is not None:
        return int(last_run_ts) + interval_h * 3600

    # Invalid spec : never run
    return 2**31 - 1


def should_run_now(entry: dict, now: Optional[int] = None) -> bool:
    """True if entry is due to run."""
    if now is None:
        import time as _t
        now = int(_t.time())
    if not entry.get("enabled", True):
        return False
    spec = entry.get("schedule", "")
    if not validate_schedule(spec):
        return False
    last_run = int(entry.get("last_run_ts") or 0)
    return next_run_ts(spec, last_run, now) <= now


def load_schedule(path: Optional[str] = None) -> list:
    """Load list of entries from JSON, returns [] if missing/invalid."""
    if path is None:
        path = os.path.expanduser("~/.config/gpu-dashboard/benchmark_schedule.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            # Filter to entries with required fields
            valid = []
            for e in data:
                if (isinstance(e, dict)
                    and "profile_a" in e and "profile_b" in e
                    and "schedule" in e and validate_schedule(e["schedule"])):
                    valid.append(e)
            return valid
        return []
    except (json.JSONDecodeError, OSError):
        return []


def save_schedule(entries: list, path: Optional[str] = None) -> None:
    """Persist the schedule list."""
    if path is None:
        path = os.path.expanduser("~/.config/gpu-dashboard/benchmark_schedule.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(entries, f, indent=2, sort_keys=True)


def due_entries(entries: list, now: Optional[int] = None) -> list:
    """Return only the entries that should fire right now."""
    return [e for e in entries if should_run_now(e, now)]

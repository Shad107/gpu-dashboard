"""Tests for the benchmark scheduler module (R&D #2.5, cycle 133)."""
import datetime
import json

import pytest

from gpu_dashboard.modules import benchmark_scheduler as bs


# ─── validate_schedule ─────────────────────────────────────────────────────


def test_validate_daily():
    assert bs.validate_schedule("daily_03")
    assert bs.validate_schedule("daily_23")
    assert bs.validate_schedule("daily_0")


def test_validate_weekly():
    assert bs.validate_schedule("weekly_sun_03")
    assert bs.validate_schedule("weekly_mon_12")


def test_validate_interval():
    assert bs.validate_schedule("interval_24")
    assert bs.validate_schedule("interval_1")


def test_validate_rejects_garbage():
    assert not bs.validate_schedule("")
    assert not bs.validate_schedule("daily_25")
    assert not bs.validate_schedule("weekly_funday_03")
    assert not bs.validate_schedule("monthly")
    assert not bs.validate_schedule(None)


# ─── next_run_ts ───────────────────────────────────────────────────────────


def test_interval_next_run_uses_last_run():
    # last_run at epoch 1000, interval 1h → next at 1000 + 3600 = 4600
    assert bs.next_run_ts("interval_1", last_run_ts=1000) == 4600


def test_daily_next_run_today_if_future():
    # now = 02:00 Mon. daily_03 → today 03:00.
    now = int(datetime.datetime(2026, 5, 25, 2, 0, 0).timestamp())  # Mon
    nxt = bs.next_run_ts("daily_03", last_run_ts=0, now=now)
    nxt_dt = datetime.datetime.fromtimestamp(nxt)
    assert nxt_dt.hour == 3
    # Same calendar day (or next day if we crossed midnight)


def test_daily_next_run_tomorrow_if_past():
    # now = 04:00 Mon. daily_03 → tomorrow 03:00.
    now = int(datetime.datetime(2026, 5, 25, 4, 0, 0).timestamp())
    nxt = bs.next_run_ts("daily_03", last_run_ts=0, now=now)
    nxt_dt = datetime.datetime.fromtimestamp(nxt)
    assert nxt_dt.hour == 3
    assert nxt > now  # strictly in the future


def test_weekly_next_run_on_target_day():
    # now = Wednesday. weekly_sun_03 → next Sunday 03:00.
    now = int(datetime.datetime(2026, 5, 27, 12, 0, 0).timestamp())  # Wed
    nxt = bs.next_run_ts("weekly_sun_03", last_run_ts=0, now=now)
    nxt_dt = datetime.datetime.fromtimestamp(nxt)
    assert nxt_dt.weekday() == 6  # Sunday
    assert nxt_dt.hour == 3


# ─── should_run_now ────────────────────────────────────────────────────────


def test_should_run_now_interval_first_run():
    """interval_1 with last_run=0 → should fire (next_run = 0 + 3600 = 3600 < now)."""
    entry = {"schedule": "interval_1", "last_run_ts": 0,
             "profile_a": "silent", "profile_b": "boost"}
    assert bs.should_run_now(entry, now=10_000)


def test_should_run_now_interval_not_yet():
    """Recent run + 24h interval → not yet."""
    entry = {"schedule": "interval_24", "last_run_ts": 10_000,
             "profile_a": "silent", "profile_b": "boost"}
    # now = 10_000 + 100 (only 100 seconds elapsed)
    assert not bs.should_run_now(entry, now=10_100)


def test_should_run_now_disabled():
    entry = {"schedule": "interval_1", "last_run_ts": 0, "enabled": False,
             "profile_a": "silent", "profile_b": "boost"}
    assert not bs.should_run_now(entry, now=10_000)


def test_should_run_now_invalid_schedule():
    entry = {"schedule": "garbage", "profile_a": "silent", "profile_b": "boost"}
    assert not bs.should_run_now(entry, now=10_000)


# ─── load / save ───────────────────────────────────────────────────────────


def test_load_returns_empty_when_missing(tmp_path):
    assert bs.load_schedule(str(tmp_path / "missing.json")) == []


def test_save_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "sch.json")
    entries = [{
        "profile_a": "silent", "profile_b": "boost",
        "schedule": "weekly_sun_03", "duration_s": 60, "last_run_ts": 0,
    }]
    bs.save_schedule(entries, path)
    loaded = bs.load_schedule(path)
    assert len(loaded) == 1
    assert loaded[0]["schedule"] == "weekly_sun_03"


def test_load_filters_invalid_entries(tmp_path):
    """Entries missing required fields or with invalid schedule should drop."""
    path = str(tmp_path / "mixed.json")
    with open(path, "w") as f:
        json.dump([
            {"profile_a": "silent", "profile_b": "boost", "schedule": "weekly_sun_03"},  # ok
            {"profile_a": "silent", "schedule": "interval_24"},  # missing profile_b
            {"profile_a": "silent", "profile_b": "boost", "schedule": "garbage"},  # bad sched
            "not even a dict",
        ], f)
    loaded = bs.load_schedule(path)
    assert len(loaded) == 1


# ─── due_entries ───────────────────────────────────────────────────────────


def test_due_entries_filters_correctly():
    entries = [
        # Will fire (interval_1 + last_run=0 + now=10k)
        {"schedule": "interval_1", "last_run_ts": 0,
         "profile_a": "silent", "profile_b": "boost", "name": "due"},
        # Will NOT fire (recent run)
        {"schedule": "interval_24", "last_run_ts": 9_000,
         "profile_a": "silent", "profile_b": "boost", "name": "not-due"},
    ]
    due = bs.due_entries(entries, now=10_000)
    assert len(due) == 1
    assert due[0]["name"] == "due"

"""Tests for modules/sched_audit.py — R&D #47.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import sched_audit as mod


SCHEDSTAT_SAMPLE = """\
version 17
timestamp 4502634507
cpu0 0 0 0 0 0 0 26911213445830 3582333561921 94465622
cpu1 0 0 0 0 0 0 100000000000 50000000000 1000000
"""


# --- parse_schedstat ----------------------------------------------

def test_parse_schedstat_basic():
    p = mod.parse_schedstat(SCHEDSTAT_SAMPLE)
    assert p["version"] == 17
    assert len(p["cpus"]) == 2
    cpu0 = p["cpus"][0]
    assert cpu0["cpu"] == 0
    assert cpu0["rq_cpu_time_ns"] == 26911213445830
    assert cpu0["run_delay_ns"] == 3582333561921
    assert cpu0["pcount"] == 94465622
    # avg = 3582333561921 // 94465622 ≈ 37920 ns
    assert 37000 < cpu0["avg_wait_ns"] < 39000


def test_parse_schedstat_empty():
    assert mod.parse_schedstat("") == {"version": None, "cpus": []}
    assert mod.parse_schedstat(None) == {"version": None, "cpus": []}


def test_parse_schedstat_no_version():
    p = mod.parse_schedstat("cpu0 0 0 0 100 200 300\n")
    assert p["version"] is None
    assert len(p["cpus"]) == 1


def test_parse_schedstat_skips_short_row():
    p = mod.parse_schedstat("version 17\ncpu0 5\n")
    assert p["cpus"] == []


def test_parse_schedstat_pcount_zero_safe():
    # avg_wait must not divide by zero
    p = mod.parse_schedstat("cpu0 0 0 0 100 0 0\n")
    assert p["cpus"][0]["avg_wait_ns"] == 0


# --- parse_sched_features ----------------------------------------

def test_parse_features_basic():
    f = mod.parse_sched_features(
        "GENTLE_FAIR_SLEEPERS NO_NEXT_BUDDY WAKEUP_PREEMPTION")
    assert f["GENTLE_FAIR_SLEEPERS"] is True
    assert f["NEXT_BUDDY"] is False
    assert f["WAKEUP_PREEMPTION"] is True


def test_parse_features_empty():
    assert mod.parse_sched_features("") == {}
    assert mod.parse_sched_features(None) == {}


# --- classify -----------------------------------------------------

def _cpu(cpu=0, avg=10_000, pcount=10_000):
    return {"cpu": cpu, "rq_cpu_time_ns": 1_000_000,
              "run_delay_ns": avg * pcount,
              "pcount": pcount, "avg_wait_ns": avg}


def test_classify_no_schedstat():
    v = mod.classify({"version": 17, "cpus": []}, {})
    assert v["verdict"] == "no_schedstat"


def test_classify_ok():
    v = mod.classify({"version": 17,
                       "cpus": [_cpu(avg=20_000, pcount=10_000)]}, {})
    assert v["verdict"] == "ok"


def test_classify_runqueue_pileup():
    v = mod.classify({"version": 17,
                       "cpus": [_cpu(cpu=8, avg=200_000,
                                       pcount=10_000)]}, {})
    assert v["verdict"] == "runqueue_wait_pileup"
    assert "CPU8" in v["reason"]


def test_classify_pileup_skipped_below_min_slices():
    # 500 slices < 1k floor → not flagged even at 500 µs avg.
    v = mod.classify({"version": 17,
                       "cpus": [_cpu(avg=500_000, pcount=500)]}, {})
    assert v["verdict"] == "ok"


def test_classify_sched_feat_hostile():
    # Drift WAKEUP_PREEMPTION → False
    v = mod.classify({"version": 17,
                       "cpus": [_cpu(avg=20_000, pcount=10_000)]},
                      {"WAKEUP_PREEMPTION": False})
    assert v["verdict"] == "sched_feat_hostile"
    assert "WAKEUP_PREEMPTION" in v["reason"]


def test_classify_pileup_wins_over_feat():
    v = mod.classify({"version": 17,
                       "cpus": [_cpu(cpu=8, avg=200_000,
                                       pcount=10_000)]},
                      {"WAKEUP_PREEMPTION": False})
    assert v["verdict"] == "runqueue_wait_pileup"


# --- status integration -------------------------------------------

def test_status_with_isolated(monkeypatch, tmp_path):
    (tmp_path / "schedstat").write_text(SCHEDSTAT_SAMPLE)
    monkeypatch.setattr(mod, "_PROC_SCHEDSTAT",
                        str(tmp_path / "schedstat"))
    monkeypatch.setattr(mod, "_DEBUGFS_SCHED",
                        str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is True
    assert out["cpu_count"] == 2
    assert out["features_readable"] is False
    # First CPU's avg wait is ~38k ns < 100 µs → verdict ok.
    assert out["verdict"]["verdict"] == "ok"


def test_status_no_schedstat(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_SCHEDSTAT",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_DEBUGFS_SCHED",
                        str(tmp_path / "nope2"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "no_schedstat"


def test_status_top_cpus_sorted(monkeypatch, tmp_path):
    # Provide 3 CPUs, last one has highest avg_wait
    text = ("version 17\n"
            "cpu0 0 0 0 0 0 0 100000000000 1000000000 1000000\n"
            "cpu1 0 0 0 0 0 0 100000000000 5000000000 1000000\n"
            "cpu2 0 0 0 0 0 0 100000000000 2000000000 1000000\n")
    (tmp_path / "schedstat").write_text(text)
    monkeypatch.setattr(mod, "_PROC_SCHEDSTAT",
                        str(tmp_path / "schedstat"))
    monkeypatch.setattr(mod, "_DEBUGFS_SCHED",
                        str(tmp_path / "nope"))
    out = mod.status()
    assert out["top_cpus_by_wait"][0]["cpu"] == 1

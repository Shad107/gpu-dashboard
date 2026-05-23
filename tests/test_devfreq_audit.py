"""Tests for modules/devfreq_audit.py — R&D #62.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import devfreq_audit as mod


def _mk_devfreq(root, name, *, governor="simple_ondemand",
                  cur_freq=500000000, min_freq=200000000,
                  max_freq=1000000000,
                  available_governors="simple_ondemand performance powersave",
                  target_freq=500000000):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "governor").write_text(governor + "\n")
    (d / "cur_freq").write_text(f"{cur_freq}\n")
    (d / "min_freq").write_text(f"{min_freq}\n")
    (d / "max_freq").write_text(f"{max_freq}\n")
    (d / "available_governors").write_text(
        available_governors + "\n")
    (d / "target_freq").write_text(f"{target_freq}\n")


# --- list_devices -----------------------------------------------

def test_list_devices_missing(tmp_path):
    assert mod.list_devices(str(tmp_path / "nope")) == []


def test_list_devices(tmp_path):
    _mk_devfreq(tmp_path, "13800000.gpu")
    _mk_devfreq(tmp_path, "ddr")
    out = mod.list_devices(str(tmp_path))
    assert len(out) == 2


# --- classify ---------------------------------------------------

def _d(name="gpu", governor="simple_ondemand",
        cur=500000000, mn=200000000, mx=1000000000):
    return {"name": name, "governor": governor,
              "cur_freq": cur, "min_freq": mn, "max_freq": mx,
              "available_governors":
                  ["simple_ondemand", "performance", "powersave"],
              "target_freq": cur}


def test_classify_unknown():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_d()])
    assert v["verdict"] == "ok"


def test_classify_stuck_min():
    v = mod.classify([_d(cur=200000000)])  # cur == min
    assert v["verdict"] == "stuck_min"


def test_classify_stuck_max_userspace_pinned():
    # userspace governor that parks at max → stuck_max fires
    # before userspace_governor (priority order).
    v = mod.classify([_d(cur=1000000000, governor="userspace")])
    assert v["verdict"] == "stuck_max"


def test_classify_stuck_max_unknown_gov():
    v = mod.classify([_d(cur=1000000000, governor="customgov")])
    assert v["verdict"] == "stuck_max"


def test_classify_userspace_governor():
    v = mod.classify([_d(governor="userspace")])
    assert v["verdict"] == "userspace_governor"


def test_classify_pinned_perf():
    v = mod.classify([_d(governor="performance")])
    assert v["verdict"] == "pinned_perf"


def test_classify_priority_stuck_min_wins():
    v = mod.classify([_d(cur=200000000, governor="performance")])
    assert v["verdict"] == "stuck_min"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    _mk_devfreq(tmp_path, "13800000.gpu",
                  governor="simple_ondemand",
                  cur_freq=500000000)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["device_count"] == 1
    assert out["verdict"]["verdict"] == "ok"

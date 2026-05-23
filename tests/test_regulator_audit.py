"""Tests for modules/regulator_audit.py — R&D #61.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import regulator_audit as mod


def _mk_regulator(root, idx, *, name="vdd_cpu", type_="voltage",
                    num_users=2, requested_microamps=0,
                    suspend_mem="disabled",
                    suspend_disk="disabled",
                    suspend_standby="disabled",
                    runtime_status="active"):
    d = root / f"regulator.{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(name + "\n")
    (d / "type").write_text(type_ + "\n")
    (d / "num_users").write_text(f"{num_users}\n")
    (d / "requested_microamps").write_text(
        f"{requested_microamps}\n")
    (d / "suspend_mem_state").write_text(suspend_mem + "\n")
    (d / "suspend_disk_state").write_text(suspend_disk + "\n")
    (d / "suspend_standby_state").write_text(
        suspend_standby + "\n")
    pwr = d / "power"
    pwr.mkdir(parents=True, exist_ok=True)
    (pwr / "runtime_status").write_text(runtime_status + "\n")
    return d


# --- list_regulators --------------------------------------------

def test_list_regulators_missing(tmp_path):
    assert mod.list_regulators(str(tmp_path / "nope")) == []


def test_list_regulators(tmp_path):
    _mk_regulator(tmp_path, 0, name="regulator-dummy",
                     num_users=1)
    _mk_regulator(tmp_path, 1, name="vdd_cpu", num_users=2)
    (tmp_path / "other-dir").mkdir()
    out = mod.list_regulators(str(tmp_path))
    assert len(out) == 2
    names = [r["name"] for r in out]
    assert "regulator-dummy" in names
    assert "vdd_cpu" in names


# --- classify ---------------------------------------------------

def _r(name="vdd_cpu", num_users=2, requested_microamps=0,
        suspend_mem="disabled", suspend_disk="disabled",
        runtime_status="active"):
    return {"id": "regulator.0", "name": name, "type": "voltage",
              "num_users": num_users,
              "requested_microamps": requested_microamps,
              "suspend_mem_state": suspend_mem,
              "suspend_disk_state": suspend_disk,
              "suspend_standby_state": "disabled",
              "runtime_status": runtime_status}


def test_classify_unknown():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_dummy_only_unknown():
    v = mod.classify([_r(name="regulator-dummy")])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_r()])
    assert v["verdict"] == "ok"


def test_classify_orphan():
    v = mod.classify([_r(num_users=0, requested_microamps=100000)])
    assert v["verdict"] == "orphan"


def test_classify_disabled_with_users():
    v = mod.classify([_r(runtime_status="suspended",
                            num_users=2)])
    assert v["verdict"] == "disabled_with_users"


def test_classify_drifted():
    v = mod.classify([_r(suspend_mem="on", suspend_disk="on")])
    assert v["verdict"] == "drifted_suspend_state"


def test_classify_priority_orphan_wins():
    # Orphan + drifted → orphan priority
    v = mod.classify([_r(num_users=0,
                            requested_microamps=50000,
                            suspend_mem="on",
                            suspend_disk="on")])
    assert v["verdict"] == "orphan"


# --- status integration -----------------------------------------

def test_status_dummy_only(tmp_path):
    _mk_regulator(tmp_path, 0, name="regulator-dummy",
                     num_users=1)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["regulator_count"] == 1
    assert out["verdict"]["verdict"] == "unknown"


def test_status_missing(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_orphan(tmp_path):
    _mk_regulator(tmp_path, 0, name="vdd_orphan",
                     num_users=0, requested_microamps=100000)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "orphan"

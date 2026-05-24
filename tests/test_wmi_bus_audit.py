"""Tests for modules/wmi_bus_audit.py — R&D #76.1."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import wmi_bus_audit as mod


_GUID_A = "8D9DDCBC-A997-11DA-B012-B622A1EF5492"
_GUID_B = "ABBC0F0A-8EA7-11D1-A1BD-008005ABCD12"


def _mk_guid(bus_root, bus_name, guid, *,
                 instance_count=1, expensive=0,
                 object_id="DELL", setable=1,
                 driver=None, driver_dangling=False,
                 modalias="wmi:" + _GUID_A):
    bdir = bus_root / bus_name
    bdir.mkdir(parents=True, exist_ok=True)
    d = bdir / guid
    d.mkdir(exist_ok=True)
    (d / "instance_count").write_text(f"{instance_count}\n")
    (d / "expensive").write_text(f"{expensive}\n")
    if object_id:
        (d / "object_id").write_text(object_id + "\n")
    (d / "setable").write_text(f"{setable}\n")
    if modalias:
        (d / "modalias").write_text(modalias + "\n")
    if driver:
        # Build a symlink target. If driver_dangling=True, point
        # to a non-existent target.
        if driver_dangling:
            target = "/sys/bus/wmi/drivers/" + driver
        else:
            real_dir = bus_root / "drivers" / driver
            real_dir.mkdir(parents=True, exist_ok=True)
            target = str(real_dir)
        os.symlink(target, str(d / "driver"))


# --- list_wmi_guids --------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_wmi_guids(str(tmp_path / "nope")) == []


def test_list_basic(tmp_path):
    _mk_guid(tmp_path, "wmi_bus-platform-...",
                 _GUID_A, instance_count=1,
                 expensive=1, driver="dell-wmi")
    _mk_guid(tmp_path, "wmi_bus-platform-...",
                 _GUID_B, instance_count=1,
                 expensive=0)
    out = mod.list_wmi_guids(str(tmp_path))
    by_guid = {g["guid"]: g for g in out}
    assert by_guid[_GUID_A]["expensive"] == 1
    assert by_guid[_GUID_A]["driver"] == "dell-wmi"
    assert by_guid[_GUID_B]["expensive"] == 0
    assert by_guid[_GUID_B]["driver"] is None


def test_list_skips_non_bus(tmp_path):
    (tmp_path / "uevent").write_text("")
    _mk_guid(tmp_path, "wmi_bus-x", _GUID_A)
    out = mod.list_wmi_guids(str(tmp_path))
    assert len(out) == 1


# --- classify ---------------------------------------------------

def _g(**overrides):
    base = {"bus": "wmi_bus-x", "guid": _GUID_A,
              "instance_count": 1, "expensive": 0,
              "object_id": "DELL", "setable": 1,
              "driver": "dell-wmi",
              "driver_dangling": False,
              "modalias": "wmi:" + _GUID_A}
    base.update(overrides)
    return base


def test_classify_unknown_absent():
    v = mod.classify(False, [])
    assert v["verdict"] == "unknown"


def test_classify_unknown_empty_present():
    v = mod.classify(True, [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(True, [_g()])
    assert v["verdict"] == "ok"


def test_classify_expensive_unbound():
    v = mod.classify(True,
                          [_g(expensive=1, driver=None)])
    assert v["verdict"] == "expensive_unbound"


def test_classify_orphan_guid():
    v = mod.classify(True, [_g(object_id=None)])
    assert v["verdict"] == "orphan_guid"


def test_classify_missing_modalias():
    v = mod.classify(True, [_g(modalias=None)])
    assert v["verdict"] == "missing_modalias"


def test_classify_stale_binding():
    v = mod.classify(True, [_g(driver_dangling=True)])
    assert v["verdict"] == "stale_binding"


# Priority : expensive_unbound > orphan > missing_modalias > stale
def test_priority_expensive_over_orphan():
    v = mod.classify(True,
                          [_g(expensive=1, driver=None,
                                object_id=None)])
    assert v["verdict"] == "expensive_unbound"


def test_priority_orphan_over_modalias():
    v = mod.classify(True,
                          [_g(object_id=None, modalias=None)])
    assert v["verdict"] == "orphan_guid"


def test_priority_modalias_over_stale():
    v = mod.classify(True,
                          [_g(modalias=None,
                                driver_dangling=True)])
    assert v["verdict"] == "missing_modalias"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_guid(tmp_path, "wmi_bus-x", _GUID_A,
                 expensive=0, driver="dell-wmi")
    _mk_guid(tmp_path, "wmi_bus-x", _GUID_B,
                 expensive=1, driver="hp-wmi")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["guid_count"] == 2
    assert out["expensive_count"] == 1
    assert out["bound_count"] == 2
    assert out["verdict"]["verdict"] == "ok"


def test_status_expensive_unbound(tmp_path):
    _mk_guid(tmp_path, "wmi_bus-x", _GUID_A,
                 expensive=1, driver=None)
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "expensive_unbound"

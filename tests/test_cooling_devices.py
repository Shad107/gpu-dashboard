"""Tests for modules/cooling_devices.py — R&D #42.3."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import cooling_devices as mod


def _mk_cdev(root: Path, idx: int, *, type_: str = "Processor",
              cur: int = 0, max_: int = 3):
    cdir = root / f"cooling_device{idx}"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "type").write_text(type_ + "\n")
    (cdir / "cur_state").write_text(str(cur) + "\n")
    (cdir / "max_state").write_text(str(max_) + "\n")


def _mk_zone(root: Path, idx: int, *, type_: str = "x86_pkg_temp",
              trips: int = 0, bindings: list | None = None):
    zdir = root / f"thermal_zone{idx}"
    zdir.mkdir(parents=True, exist_ok=True)
    (zdir / "type").write_text(type_ + "\n")
    for i in range(trips):
        (zdir / f"trip_point_{i}_type").write_text("active\n")
        (zdir / f"trip_point_{i}_temp").write_text("80000\n")
    for b in bindings or []:
        slot = b["slot"]
        target_dir = root / f"cooling_device{b['target']}"
        if not target_dir.exists():
            _mk_cdev(root, b["target"])
        try:
            (zdir / f"cdev{slot}").symlink_to(target_dir)
        except FileExistsError:
            pass
        (zdir / f"cdev{slot}_trip_point").write_text(
            str(b.get("trip", 0)) + "\n")
        (zdir / f"cdev{slot}_weight").write_text(
            str(b.get("weight", 0)) + "\n")


# --- list_cooling_devices ------------------------------------------

def test_list_cooling_devices_empty(tmp_path):
    assert mod.list_cooling_devices(str(tmp_path / "nope")) == []


def test_list_cooling_devices_sorted_by_index(tmp_path):
    # Create out of order — module should sort numerically.
    _mk_cdev(tmp_path, 10, type_="Processor")
    _mk_cdev(tmp_path, 2, type_="Processor")
    _mk_cdev(tmp_path, 0, type_="Fan")
    cs = mod.list_cooling_devices(str(tmp_path))
    assert [c["index"] for c in cs] == [0, 2, 10]
    assert cs[0]["type"] == "Fan"


def test_list_cooling_devices_skips_other_entries(tmp_path):
    _mk_cdev(tmp_path, 0)
    (tmp_path / "thermal_zone0").mkdir()
    (tmp_path / "uevent").write_text("\n")
    cs = mod.list_cooling_devices(str(tmp_path))
    assert len(cs) == 1


def test_list_cooling_devices_missing_files(tmp_path):
    (tmp_path / "cooling_device3").mkdir()
    cs = mod.list_cooling_devices(str(tmp_path))
    assert cs[0]["cur_state"] is None
    assert cs[0]["max_state"] is None


# --- list_thermal_zones --------------------------------------------

def test_list_thermal_zones(tmp_path):
    _mk_zone(tmp_path, 0)
    _mk_zone(tmp_path, 1)
    _mk_cdev(tmp_path, 0)
    out = mod.list_thermal_zones(str(tmp_path))
    assert out == ["thermal_zone0", "thermal_zone1"]


# --- read_zone_bindings --------------------------------------------

def test_read_zone_bindings_no_bindings(tmp_path):
    _mk_zone(tmp_path, 0, trips=2)
    out = mod.read_zone_bindings(str(tmp_path), "thermal_zone0")
    assert out["trip_count"] == 2
    assert out["bindings"] == []


def test_read_zone_bindings_with_one_binding(tmp_path):
    _mk_zone(tmp_path, 0, trips=2, bindings=[
        {"slot": 0, "target": 5, "trip": 1, "weight": 100},
    ])
    out = mod.read_zone_bindings(str(tmp_path), "thermal_zone0")
    assert len(out["bindings"]) == 1
    b = out["bindings"][0]
    assert b["cdev_slot"] == 0
    assert b["cdev_target"] == "cooling_device5"
    assert b["cdev_index"] == 5
    assert b["trip_point"] == 1
    assert b["weight"] == 100


# --- classify ------------------------------------------------------

def _cdev(idx, type_="Processor", cur=0, max_=3):
    return {"name": f"cooling_device{idx}", "index": idx,
              "type": type_, "cur_state": cur, "max_state": max_}


def _zone(zone="thermal_zone0", trips=0, bindings=None, type_="cpu"):
    return {"zone": zone, "type": type_,
              "trip_count": trips,
              "bindings": bindings or [],
              "cdevs_present_count": len(bindings or [])}


def test_classify_unknown_when_nothing():
    v = mod.classify([], [])
    assert v["verdict"] == "unknown"


def test_classify_no_cooling_when_only_zones():
    v = mod.classify([], [_zone(trips=1)])
    assert v["verdict"] == "no_cooling"


def test_classify_ok_when_cdevs_present_idle():
    v = mod.classify([_cdev(0, cur=0, max_=3),
                        _cdev(1, cur=1, max_=3)], [])
    assert v["verdict"] == "ok"


def test_classify_saturated_cdev():
    v = mod.classify([
        _cdev(0, cur=0, max_=3),
        _cdev(1, type_="Processor", cur=3, max_=3),
    ], [_zone(trips=1, bindings=[{"slot": 0, "target": 1}])])
    assert v["verdict"] == "saturated_cdev"
    assert "cooling_device1" in v["reason"]


def test_classify_skips_zero_max_state_in_saturation():
    # Processor cdevs in idle states often report max_state=0 ;
    # cur=max=0 is not "saturated", just idle.
    v = mod.classify([_cdev(0, cur=0, max_=0)], [])
    assert v["verdict"] == "ok"


def test_classify_unbound_zone():
    v = mod.classify([_cdev(0, cur=0, max_=3)],
                       [_zone(trips=2, bindings=[])])
    assert v["verdict"] == "unbound_zone"


def test_classify_zone_no_trips_is_ok():
    v = mod.classify([_cdev(0, cur=0, max_=3)],
                       [_zone(trips=0, bindings=[])])
    assert v["verdict"] == "ok"


def test_classify_priority_saturated_over_unbound():
    v = mod.classify([_cdev(0, cur=3, max_=3)],
                       [_zone(trips=2, bindings=[])])
    assert v["verdict"] == "saturated_cdev"


# --- status integration --------------------------------------------

def test_status_with_cdevs_no_zones(monkeypatch, tmp_path):
    for i in range(3):
        _mk_cdev(tmp_path, i, type_="Processor", cur=0, max_=0)
    monkeypatch.setattr(mod, "_SYS_THERMAL", str(tmp_path))
    out = mod.status()
    assert out["ok"] is True
    assert out["cooling_device_count"] == 3
    assert out["thermal_zone_count"] == 0
    assert out["verdict"]["verdict"] == "ok"


def test_status_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_THERMAL", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_full_layout(monkeypatch, tmp_path):
    _mk_cdev(tmp_path, 0, type_="Fan", cur=2, max_=4)
    _mk_cdev(tmp_path, 1, type_="Processor", cur=0, max_=3)
    _mk_zone(tmp_path, 0, type_="cpu_thermal", trips=2, bindings=[
        {"slot": 0, "target": 1, "trip": 0, "weight": 100},
    ])
    monkeypatch.setattr(mod, "_SYS_THERMAL", str(tmp_path))
    out = mod.status()
    assert out["cooling_device_count"] == 2
    assert out["thermal_zone_count"] == 1
    assert out["thermal_zones"][0]["bindings"][0]["cdev_index"] == 1
    assert out["verdict"]["verdict"] == "ok"

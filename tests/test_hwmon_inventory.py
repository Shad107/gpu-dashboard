"""Tests for modules/hwmon_inventory.py — R&D #31.1 hwmon parity."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import hwmon_inventory


def _mk_hwmon(root: Path, n: int, *, name: str,
                 temps: dict | None = None,
                 fans: dict | None = None):
    """temps = {1: ("Composite", 62000, 84000)} → temp1_label=Composite,
    temp1_input=62000 mC, temp1_max=84000 mC.
    fans  = {1: ("CPU Fan", 1450)}  → fan1_label=, fan1_input=1450 RPM."""
    d = root / f"hwmon{n}"
    d.mkdir(parents=True)
    (d / "name").write_text(name + "\n")
    for k, v in (temps or {}).items():
        label, input_mC, max_mC = v
        if label:
            (d / f"temp{k}_label").write_text(label + "\n")
        (d / f"temp{k}_input").write_text(f"{input_mC}\n")
        if max_mC:
            (d / f"temp{k}_max").write_text(f"{max_mC}\n")
    for k, v in (fans or {}).items():
        label, rpm = v
        if label:
            (d / f"fan{k}_label").write_text(label + "\n")
        (d / f"fan{k}_input").write_text(f"{rpm}\n")


# --- helpers ---------------------------------------------------------

def test_parse_temp_millidegrees_to_c():
    assert hwmon_inventory.parse_temp_mC("62000") == 62.0
    assert hwmon_inventory.parse_temp_mC("84500") == 84.5


def test_parse_temp_negative():
    # Some sensors report transient -1 °C
    assert hwmon_inventory.parse_temp_mC("-1000") == -1.0


def test_parse_temp_garbage_returns_none():
    assert hwmon_inventory.parse_temp_mC("") is None
    assert hwmon_inventory.parse_temp_mC(None) is None
    assert hwmon_inventory.parse_temp_mC("junk") is None


def test_list_hwmons_empty(tmp_path):
    assert hwmon_inventory.list_hwmons(str(tmp_path / "absent")) == []


def test_list_hwmons_sorted_numerically(tmp_path):
    for n in (10, 2, 1):
        (tmp_path / f"hwmon{n}").mkdir()
    assert hwmon_inventory.list_hwmons(str(tmp_path)) == [
        "hwmon1", "hwmon2", "hwmon10",
    ]


def test_list_hwmons_ignores_other(tmp_path):
    (tmp_path / "hwmon0").mkdir()
    (tmp_path / "weird").mkdir()
    assert hwmon_inventory.list_hwmons(str(tmp_path)) == ["hwmon0"]


# --- name → kind decoding ------------------------------------------

def test_detect_kind_nvme():
    assert hwmon_inventory.detect_kind("nvme") == "NVMe"


def test_detect_kind_coretemp():
    assert hwmon_inventory.detect_kind("coretemp") == "CPU"


def test_detect_kind_k10temp():
    assert hwmon_inventory.detect_kind("k10temp") == "CPU"


def test_detect_kind_amdgpu():
    assert hwmon_inventory.detect_kind("amdgpu") == "iGPU"


def test_detect_kind_acpitz():
    assert hwmon_inventory.detect_kind("acpitz") == "Chassis"


def test_detect_kind_nct_super_io():
    assert hwmon_inventory.detect_kind("nct6796") == "SuperIO"


def test_detect_kind_unknown():
    assert hwmon_inventory.detect_kind("weirdsensor") == "Other"


# --- read_hwmon ----------------------------------------------------

def test_read_hwmon_collects_temps(tmp_path):
    _mk_hwmon(tmp_path, 0, name="nvme",
                 temps={1: ("Composite", 62000, 84000),
                          2: ("Sensor 1", 58000, 82000)})
    h = hwmon_inventory.read_hwmon(str(tmp_path), "hwmon0")
    assert h["name"] == "nvme"
    assert h["kind"] == "NVMe"
    assert len(h["sensors"]) == 2
    s1 = next(s for s in h["sensors"] if s["channel"] == 1)
    assert s1["label"] == "Composite"
    assert s1["value_c"] == 62.0
    assert s1["max_c"] == 84.0


def test_read_hwmon_collects_fans(tmp_path):
    _mk_hwmon(tmp_path, 0, name="nct6796",
                 fans={1: ("CPU Fan", 1450), 2: (None, 0)})
    h = hwmon_inventory.read_hwmon(str(tmp_path), "hwmon0")
    assert len(h["fans"]) == 2


def test_read_hwmon_handles_temp_only(tmp_path):
    _mk_hwmon(tmp_path, 0, name="coretemp",
                 temps={1: ("Package", 55000, 100000)})
    h = hwmon_inventory.read_hwmon(str(tmp_path), "hwmon0")
    assert len(h["sensors"]) == 1
    assert h["fans"] == []


# --- classify ------------------------------------------------------

def test_classify_clean_at_idle_temps():
    sensors = [
        {"kind": "NVMe", "label": "Composite", "value_c": 45.0,
         "max_c": 84.0},
        {"kind": "CPU", "label": "Package", "value_c": 50.0,
         "max_c": 100.0},
    ]
    v = hwmon_inventory.classify(sensors)
    assert v["verdict"] == "clean"


def test_classify_nvme_hot_at_75():
    sensors = [
        {"kind": "NVMe", "label": "Composite", "value_c": 78.0,
         "max_c": 84.0},
    ]
    v = hwmon_inventory.classify(sensors)
    assert v["verdict"] == "nvme_hot"
    assert "78" in v["reason"] or "NVMe" in v["reason"]
    assert "airflow" in v["recommendation"].lower() or "thermal" in v["recommendation"].lower()


def test_classify_chipset_hot_at_85():
    sensors = [
        {"kind": "Chassis", "label": "PCH", "value_c": 90.0,
         "max_c": 110.0},
    ]
    v = hwmon_inventory.classify(sensors)
    assert v["verdict"] == "chipset_hot"


def test_classify_cpu_hot_at_95():
    sensors = [
        {"kind": "CPU", "label": "Tctl", "value_c": 95.0,
         "max_c": 100.0},
    ]
    v = hwmon_inventory.classify(sensors)
    assert v["verdict"] == "cpu_hot"


def test_classify_picks_worst_kind():
    sensors = [
        {"kind": "NVMe", "label": "Composite", "value_c": 78.0,
         "max_c": 84.0},
        {"kind": "CPU", "label": "Package", "value_c": 95.0,
         "max_c": 100.0},
    ]
    v = hwmon_inventory.classify(sensors)
    # CPU at 95 °C is more urgent than NVMe at 78
    assert v["verdict"] == "cpu_hot"


def test_classify_empty_sensors_no_hwmon():
    v = hwmon_inventory.classify([])
    assert v["verdict"] == "no_hwmon"


# --- status --------------------------------------------------------

def test_status_no_hwmon_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(hwmon_inventory, "_HWMON_ROOT",
                          str(tmp_path / "absent"))
    s = hwmon_inventory.status()
    assert s["ok"] is True
    assert s["device_count"] == 0
    assert s["worst_verdict"] == "no_hwmon"


def test_status_full_layout(tmp_path, monkeypatch):
    _mk_hwmon(tmp_path, 0, name="coretemp",
                 temps={1: ("Package id 0", 52000, 100000)})
    _mk_hwmon(tmp_path, 1, name="nvme",
                 temps={1: ("Composite", 62000, 84000)})
    _mk_hwmon(tmp_path, 2, name="nct6796",
                 temps={1: ("MB", 35000, 100000)},
                 fans={1: ("CPU FAN", 1480)})
    monkeypatch.setattr(hwmon_inventory, "_HWMON_ROOT", str(tmp_path))
    s = hwmon_inventory.status()
    assert s["device_count"] == 3
    assert s["worst_verdict"] == "clean"
    # All kinds captured
    kinds = {d["kind"] for d in s["devices"]}
    assert kinds >= {"CPU", "NVMe", "SuperIO"}


def test_status_surfaces_nvme_hot(tmp_path, monkeypatch):
    _mk_hwmon(tmp_path, 0, name="nvme",
                 temps={1: ("Composite", 79000, 84000)})  # 79 °C
    monkeypatch.setattr(hwmon_inventory, "_HWMON_ROOT", str(tmp_path))
    s = hwmon_inventory.status()
    assert s["worst_verdict"] == "nvme_hot"
    assert s["devices"][0]["sensors"][0]["value_c"] == 79.0


def test_status_aggregates_max_temp(tmp_path, monkeypatch):
    _mk_hwmon(tmp_path, 0, name="nvme",
                 temps={1: ("Composite", 62000, 84000),
                          2: ("Sensor 1", 58000, 82000)})
    _mk_hwmon(tmp_path, 1, name="coretemp",
                 temps={1: ("Package", 55000, 100000)})
    monkeypatch.setattr(hwmon_inventory, "_HWMON_ROOT", str(tmp_path))
    s = hwmon_inventory.status()
    assert s["max_temp_c"] == 62.0


def test_status_excludes_gpu_named_hwmon(tmp_path, monkeypatch):
    # nvidia driver doesn't typically expose /sys/class/hwmon, but
    # if it ever did (or AMD's amdgpu), we don't want to confuse the
    # NVMe airflow advisory with the discrete-GPU thermals — those
    # are covered by other modules.
    _mk_hwmon(tmp_path, 0, name="amdgpu",
                 temps={1: ("edge", 70000, 110000)})
    _mk_hwmon(tmp_path, 1, name="nvme",
                 temps={1: ("Composite", 65000, 84000)})
    monkeypatch.setattr(hwmon_inventory, "_HWMON_ROOT", str(tmp_path))
    s = hwmon_inventory.status()
    # The amdgpu sensor still appears in the inventory (informational)
    # but the verdict only considers non-GPU sensors for nvme_hot etc.
    assert any(d["kind"] == "iGPU" for d in s["devices"])
    assert s["worst_verdict"] == "clean"

"""Tests for modules/iio_sensor_audit.py — R&D #50.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import iio_sensor_audit as mod


def _mk_iio(root, name="iio:device0", driver="bmi160-accel",
              **attrs):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(driver + "\n")
    for k, v in attrs.items():
        (d / k).write_text(str(v) + "\n")


# --- list_iio_devices --------------------------------------------

def test_list_iio_missing(tmp_path):
    assert mod.list_iio_devices(str(tmp_path / "nope")) == []


def test_list_iio_empty(tmp_path):
    assert mod.list_iio_devices(str(tmp_path)) == []


def test_list_iio_basic(tmp_path):
    _mk_iio(tmp_path, "iio:device0", driver="bmi160-accel",
              in_accel_x_raw=100)
    _mk_iio(tmp_path, "iio:device1", driver="bmp280",
              in_temp_input=25000, in_pressure_input=101325)
    out = mod.list_iio_devices(str(tmp_path))
    assert len(out) == 2
    accel = next(d for d in out if d["driver_name"] == "bmi160-accel")
    assert accel["in_accel_x_raw"] == 100


def test_list_iio_skips_non_iio_dirs(tmp_path):
    _mk_iio(tmp_path, "iio:device0")
    (tmp_path / "iiochip").mkdir()
    out = mod.list_iio_devices(str(tmp_path))
    assert len(out) == 1


# --- classify_sensor_type ----------------------------------------

def test_sensor_type_chassis_intrusion():
    assert mod.classify_sensor_type({"in_intrusion0_raw": 0}) \
        == "chassis_intrusion"


def test_sensor_type_ambient_light():
    assert mod.classify_sensor_type({"in_illuminance_input": 100}) \
        == "ambient_light"


def test_sensor_type_accel():
    assert mod.classify_sensor_type({"in_accel_x_raw": 0}) \
        == "accelerometer"


def test_sensor_type_barometer():
    assert mod.classify_sensor_type({"in_pressure_input": 101325}) \
        == "barometer"


def test_sensor_type_other():
    assert mod.classify_sensor_type({"foo": 1}) == "other"


# --- classify ----------------------------------------------------

def test_classify_no_iio():
    v = mod.classify([])
    assert v["verdict"] == "no_iio"


def test_classify_chassis_intrusion():
    v = mod.classify([{"name": "iio:device0", "driver_name": "intel-ish",
                          "in_intrusion0_raw": 1}])
    assert v["verdict"] == "chassis_intrusion"


def test_classify_inventory():
    v = mod.classify([{"name": "iio:device0", "driver_name": "bmp280"}])
    assert v["verdict"] == "sensor_inventory"


def test_classify_priority_intrusion_wins():
    devs = [{"name": "iio:device0", "driver_name": "bmp280"},
              {"name": "iio:device1", "driver_name": "intrusion",
                "in_intrusion0_raw": 1}]
    v = mod.classify(devs)
    assert v["verdict"] == "chassis_intrusion"


# --- status integration ------------------------------------------

def test_status_no_iio(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_BUS_IIO", str(tmp_path / "nope"))
    out = mod.status()
    assert out["verdict"]["verdict"] == "no_iio"


def test_status_with_sensors(monkeypatch, tmp_path):
    sysiio = tmp_path / "iio"
    _mk_iio(sysiio, "iio:device0", driver="bme280",
              in_temp_input=25000, in_pressure_input=101325)
    monkeypatch.setattr(mod, "_SYS_BUS_IIO", str(sysiio))
    out = mod.status()
    assert out["device_count"] == 1
    assert out["devices"][0]["sensor_type"] == "barometer"
    assert out["verdict"]["verdict"] == "sensor_inventory"

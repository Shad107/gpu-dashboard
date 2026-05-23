"""Module iio_sensor_audit — IIO sensor inventory (R&D #50.3).

Walks /sys/bus/iio/devices/iio:device*/ for ambient-light, accel,
gyro, magnetometer, chassis-intrusion, and third-party env sensors
(BME280 temperature/pressure/humidity, MS5611 altimeter, BMP280,
ADXL345 accelerometer, etc.).

Each IIO device exposes :
  name                  driver name (`als`, `bmi160-accel`,
                        `bmp280`, `inv-mpu6050`, ...)
  in_intrusion0_raw     chassis-intrusion flag (Dell PowerEdge,
                        ASUS WS)
  in_illuminance_input  ambient-light sensor reading
  in_temp_input         temperature
  in_pressure_input     barometric pressure
  in_accel_<x|y|z>_raw  accelerometer axis
  sampling_frequency    Hz

Verdicts (priority-ordered) :
  chassis_intrusion        in_intrusion0_raw == 1 → chassis was
                           opened (Dell PowerEdge alarm wire).
  sensor_inventory         ≥1 IIO device present → surface info
                           with type-classification.
  no_iio                   /sys/bus/iio/devices empty (typical for
                           desktops + VMs without sensor hub).
  unknown                  /sys/bus/iio unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "iio_sensor_audit"


_SYS_BUS_IIO = "/sys/bus/iio/devices"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def list_iio_devices(sys_iio: str = _SYS_BUS_IIO) -> list:
    if not os.path.isdir(sys_iio):
        return []
    out: list = []
    try:
        for name in sorted(os.listdir(sys_iio)):
            if not name.startswith("iio:device"):
                continue
            d = os.path.join(sys_iio, name)
            rec: dict = {
                "name": name,
                "driver_name": (_read(os.path.join(d, "name"))
                                    or "").strip() or None,
                "sampling_frequency": _read_int(
                    os.path.join(d, "sampling_frequency")),
            }
            # Probe known sensor attribute files.
            for attr in ("in_intrusion0_raw",
                          "in_illuminance_input",
                          "in_illuminance_raw",
                          "in_temp_input",
                          "in_temp_raw",
                          "in_pressure_input",
                          "in_humidityrelative_input",
                          "in_accel_x_raw",
                          "in_accel_y_raw",
                          "in_accel_z_raw",
                          "in_anglvel_x_raw"):
                v = _read_int(os.path.join(d, attr))
                if v is not None:
                    rec[attr] = v
            out.append(rec)
    except OSError:
        return []
    return out


def classify_sensor_type(d: dict) -> str:
    """Roughly bucket the device by what attribute keys it exposes."""
    if d.get("in_intrusion0_raw") is not None:
        return "chassis_intrusion"
    if (d.get("in_illuminance_input") is not None
            or d.get("in_illuminance_raw") is not None):
        return "ambient_light"
    if d.get("in_accel_x_raw") is not None:
        return "accelerometer"
    if d.get("in_anglvel_x_raw") is not None:
        return "gyroscope"
    if d.get("in_pressure_input") is not None:
        return "barometer"
    if (d.get("in_temp_input") is not None
            or d.get("in_temp_raw") is not None):
        return "thermometer"
    if d.get("in_humidityrelative_input") is not None:
        return "humidity"
    return "other"


_RECIPE_INTRUSION = (
    "# Chassis-intrusion sensor reports 1 — the case was opened.\n"
    "# On Dell PowerEdge / ASUS WS this triggers a BIOS event +\n"
    "# can fire an SNMP trap. Investigate :\n"
    "dmesg | grep -i intrusion\n"
    "journalctl --since '1 day ago' | grep -i intrusion\n"
    "# Reset the latched intrusion bit (varies by vendor) :\n"
    "echo 0 | sudo tee /sys/bus/iio/devices/<DEV>/in_intrusion0_raw"
)


def classify(devices: list) -> dict:
    if not devices:
        return {"verdict": "no_iio",
                "reason": ("/sys/bus/iio/devices empty — no IIO "
                           "sensors exposed (typical for desktops "
                           "+ VMs without sensor hub)."),
                "recommendation": ""}
    intrusion = [d for d in devices
                   if d.get("in_intrusion0_raw") == 1]
    if intrusion:
        names = ", ".join(
            f"{d.get('driver_name') or d['name']}" for d in intrusion)
        return {"verdict": "chassis_intrusion",
                "reason": (f"{len(intrusion)} chassis-intrusion "
                           f"sensor(s) latched ACTIVE : {names}. "
                           f"Case was opened."),
                "recommendation": _RECIPE_INTRUSION}
    return {"verdict": "sensor_inventory",
            "reason": (f"{len(devices)} IIO sensor(s) inventoried — "
                       f"surface for visibility."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_BUS_IIO):
        return {
            "ok": False,
            "verdict": {"verdict": "no_iio",
                         "reason": ("/sys/bus/iio absent (CONFIG_IIO=n "
                                    "or no IIO devices)."),
                         "recommendation": ""},
            "devices": [],
        }
    devices = list_iio_devices(_SYS_BUS_IIO)
    for d in devices:
        d["sensor_type"] = classify_sensor_type(d)
    verdict = classify(devices)
    return {
        "ok": True,
        "device_count": len(devices),
        "devices": devices,
        "verdict": verdict,
    }

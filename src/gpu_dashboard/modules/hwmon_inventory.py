"""Module hwmon_inventory — non-GPU thermal + fan inventory (R&D #31.1).

Shipped #28.5 thermal_zones reads /sys/class/thermal/thermal_zone* and
correlates against GPU throttle events. But many drivers (NVMe SSD,
chipset PCH, SuperIO fan controllers) only expose their sensors via
/sys/class/hwmon/, not /sys/class/thermal/ — so a hot M.2 NVMe right
under the GPU never shows up in the existing thermal-zone view.

This module enumerates /sys/class/hwmon/hwmon<n>/, decodes each
sensor by its `name` file (nvme → NVMe, coretemp/k10temp → CPU,
nct6796/it87 → SuperIO, acpitz → Chassis, amdgpu → iGPU), reads
temp<N>_input + temp<N>_label + temp<N>_max in millidegrees C, and
classifies the host:

  clean        all non-GPU sensors well below their max (idle)
  nvme_hot     NVMe >= 75 °C → throttling risk, airflow advice
  chipset_hot  Chassis/SuperIO >= 85 °C → fan died on the chipset
  cpu_hot      CPU package >= 90 °C → cooler problem
  no_hwmon     no /sys/class/hwmon entries (VM, exotic kernel)

The verdict deliberately excludes the iGPU bucket — the discrete
NVIDIA GPU temp is covered by shipped #19.x throttle classifiers,
and AMDGPU iGPU temps are noise for an inference-anchored
dashboard.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "hwmon_inventory"


_HWMON_ROOT = "/sys/class/hwmon"


_KIND_MAP = (
    # Order matters: longest / most-specific patterns first
    ("nvme", "NVMe"),
    ("coretemp", "CPU"),
    ("k10temp", "CPU"),
    ("zenpower", "CPU"),
    ("amdgpu", "iGPU"),
    ("i915", "iGPU"),
    ("xe", "iGPU"),
    ("nct67", "SuperIO"),
    ("nct6", "SuperIO"),
    ("it87", "SuperIO"),
    ("f71808", "SuperIO"),
    ("asus", "EC"),
    ("acpitz", "Chassis"),
    ("acpi", "Chassis"),
    ("drivetemp", "Disk"),
    ("scsi_disk", "Disk"),
)


def detect_kind(name: str) -> str:
    if not name:
        return "Other"
    low = name.lower().strip()
    for prefix, kind in _KIND_MAP:
        if low.startswith(prefix) or prefix in low:
            return kind
    return "Other"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def parse_temp_mC(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    try:
        return int(s.strip()) / 1000.0
    except (ValueError, AttributeError):
        return None


_NUM_RE = re.compile(r"^hwmon(\d+)$")


def list_hwmons(root: str = _HWMON_ROOT) -> list[str]:
    try:
        names = os.listdir(root)
    except OSError:
        return []
    pairs: list = []
    for n in names:
        m = _NUM_RE.match(n)
        if m and os.path.isdir(os.path.join(root, n)):
            pairs.append((int(m.group(1)), n))
    pairs.sort()
    return [p[1] for p in pairs]


_TEMP_INPUT_RE = re.compile(r"^temp(\d+)_input$")
_FAN_INPUT_RE = re.compile(r"^fan(\d+)_input$")


def read_hwmon(root: str, hwmon: str) -> dict:
    base = os.path.join(root, hwmon)
    name = _read(os.path.join(base, "name")) or hwmon
    kind = detect_kind(name)
    sensors: list = []
    fans: list = []
    try:
        files = os.listdir(base)
    except OSError:
        files = []
    for fn in sorted(files):
        m = _TEMP_INPUT_RE.match(fn)
        if m:
            ch = int(m.group(1))
            sensors.append({
                "channel": ch,
                "label": _read(os.path.join(base, f"temp{ch}_label")),
                "value_c": parse_temp_mC(
                    _read(os.path.join(base, f"temp{ch}_input"))),
                "max_c": parse_temp_mC(
                    _read(os.path.join(base, f"temp{ch}_max"))),
                "kind": kind,
            })
            continue
        m = _FAN_INPUT_RE.match(fn)
        if m:
            ch = int(m.group(1))
            val = _read(os.path.join(base, f"fan{ch}_input"))
            try:
                rpm = int(val) if val is not None else None
            except ValueError:
                rpm = None
            fans.append({
                "channel": ch,
                "label": _read(os.path.join(base, f"fan{ch}_label")),
                "rpm": rpm,
            })
    return {"hwmon": hwmon, "name": name, "kind": kind,
            "sensors": sensors, "fans": fans}


_THRESHOLDS = {
    "NVMe":    75.0,  # NVMe TJMax typically 84-95
    "Chassis": 85.0,
    "SuperIO": 85.0,
    "EC":      85.0,
    "CPU":     90.0,  # TJMax often 100
    "Disk":    55.0,  # spinners
}


_VERDICT_PER_KIND = {
    "NVMe": "nvme_hot",
    "Chassis": "chipset_hot",
    "SuperIO": "chipset_hot",
    "EC": "chipset_hot",
    "CPU": "cpu_hot",
    "Disk": "disk_hot",
}


_RANK = {
    "clean": 0,
    "no_hwmon": 0,
    "disk_hot": 1,
    "nvme_hot": 2,
    "chipset_hot": 3,
    "cpu_hot": 4,
}


def classify(sensors: list) -> dict:
    if not sensors:
        return {"verdict": "no_hwmon",
                "reason": ("No /sys/class/hwmon sensors exposed on this "
                           "host — typical for VMs (hypervisor owns "
                           "hwmon) or kernels without sensor drivers."),
                "recommendation": ""}
    # Filter out the GPU buckets — those are covered elsewhere
    candidates = [s for s in sensors if s["kind"] not in ("iGPU",)]
    worst_v = "clean"
    worst_s = None
    for s in candidates:
        v = s.get("value_c")
        if v is None:
            continue
        thr = _THRESHOLDS.get(s["kind"])
        if thr is None or v < thr:
            continue
        verdict = _VERDICT_PER_KIND.get(s["kind"], "clean")
        if _RANK.get(verdict, 0) > _RANK.get(worst_v, 0):
            worst_v = verdict
            worst_s = s
    if worst_v == "clean":
        return {"verdict": "clean",
                "reason": "All non-GPU sensors are below their warning thresholds.",
                "recommendation": ""}
    label = worst_s.get("label") or f"channel {worst_s['channel']}"
    reason = (f"{worst_s['kind']} sensor '{label}' reads "
              f"{worst_s['value_c']:.1f} °C, above the "
              f"{_THRESHOLDS[worst_s['kind']]:.0f} °C threshold.")
    rec_map = {
        "nvme_hot": (
            "# NVMe is throttling territory. Check airflow:\n"
            "# - Add a heatsink (M.2 NVMe slots near the GPU starve for air)\n"
            "# - Verify the GPU isn't directly blowing exhaust on the NVMe\n"
            "# - Cap NVMe write throughput temporarily:\n"
            "#   echo 1024 | sudo tee /sys/block/nvmeXn1/queue/max_sectors_kb"
        ),
        "chipset_hot": (
            "# Chipset/SuperIO temp is high. Check chipset fan + heatsink:\n"
            "# - Many X570 / Z690 boards have a small chipset fan that "
            "fails first\n"
            "# - Inspect with `sensors` (lm-sensors) for fan RPM readings\n"
            "# - Reseat the chipset heatsink"
        ),
        "cpu_hot": (
            "# CPU package is near TJMax. Stop heavy workloads + inspect:\n"
            "# - CPU cooler mount + thermal paste age (>3 years → repaste)\n"
            "# - Case airflow + dust on intake filters\n"
            "# - Consider lowering PPT/PL2 cap until cooled"
        ),
        "disk_hot": (
            "# Spinning rust is unusually hot — improve airflow over the\n"
            "# drive cage and verify it isn't being touched by inference.\n"
        ),
    }
    return {"verdict": worst_v, "reason": reason,
            "recommendation": rec_map.get(worst_v, "")}


def status(cfg=None) -> dict:
    hwmons = list_hwmons(_HWMON_ROOT)
    if not hwmons:
        return {"ok": True, "device_count": 0, "devices": [],
                "worst_verdict": "no_hwmon", "max_temp_c": None}
    devices: list = []
    all_sensors: list = []
    max_temp: Optional[float] = None
    for h in hwmons:
        d = read_hwmon(_HWMON_ROOT, h)
        devices.append(d)
        for s in d["sensors"]:
            all_sensors.append(s)
            v = s.get("value_c")
            if v is not None and (max_temp is None or v > max_temp):
                max_temp = v
    verdict = classify(all_sensors)
    return {"ok": True, "device_count": len(devices),
            "devices": devices, "verdict": verdict,
            "worst_verdict": verdict["verdict"],
            "max_temp_c": max_temp}

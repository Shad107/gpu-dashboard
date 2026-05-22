"""Module disk_health — SMART attribute correlator (R&D #12.2).

Model loading can thrash NVMe/SATA — users often blame the GPU when
it's actually a dying SSD bottlenecking ingest. This module shells
out to smartctl + parses the JSON output for the disks hosting the
HF cache + user models dirs.

Surfaced attributes (per disk) :
  - temperature (°C)
  - power_on_hours
  - reallocated_sectors        (relocated bad sectors — should be 0)
  - pending_sectors            (currently flagged unreadable — should be 0)
  - media_wearout_pct          (SSD wear leveling counter — 100=new, 0=eol)
  - data_units_written_tb      (NVMe : how much has been written)
  - critical_warning_flags     (NVMe : any non-zero = serious)

Verdict :
  ok       all clean
  warn     wearout < 20% OR pending_sectors > 0 OR temp > 70°C
  fail     wearout < 5% OR critical_warning_flags > 0

stdlib only : subprocess + json. Requires smartctl on PATH (most distros
ship it via 'smartmontools').
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Optional


NAME = "disk_health"


def has_smartctl() -> bool:
    return shutil.which("smartctl") is not None


def list_devices() -> list:
    """List candidate block devices : /dev/sd* and /dev/nvme[0-9]n[0-9]."""
    devs: list = []
    try:
        for entry in sorted(os.listdir("/dev")):
            # sdX (not sdX1, sdX2…)
            if entry.startswith("sd") and len(entry) == 3 and entry[2].isalpha():
                devs.append(f"/dev/{entry}")
            # nvme0n1 (not nvme0n1p1) — pattern nvme<int>n<int>
            elif entry.startswith("nvme") and "p" not in entry:
                parts = entry.replace("nvme", "").split("n")
                if len(parts) == 2 and all(p.isdigit() for p in parts):
                    devs.append(f"/dev/{entry}")
    except OSError:
        pass
    return devs


def run_smartctl(device: str, timeout: float = 4.0) -> Optional[dict]:
    """smartctl -A -i -j <device>. Returns parsed JSON or None on failure."""
    try:
        r = subprocess.run(
            ["smartctl", "-A", "-i", "-j", device],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    # smartctl returns non-zero on certain bit flags but still writes valid JSON
    if not r.stdout:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def _attr_value(attrs: list, name_or_id) -> Optional[int]:
    """Find a SATA attribute by name or id ; return raw.value as int."""
    for a in attrs or []:
        if a.get("name", "").lower() == str(name_or_id).lower() \
           or str(a.get("id", "")) == str(name_or_id):
            raw = a.get("raw", {}).get("value")
            if raw is not None:
                try:
                    return int(raw)
                except (ValueError, TypeError):
                    pass
            val = a.get("value")
            if val is not None:
                try:
                    return int(val)
                except (ValueError, TypeError):
                    pass
    return None


def parse_smart(data: dict) -> dict:
    """Normalize the smartctl JSON into a compact dict suitable for UI/alerts."""
    if not isinstance(data, dict):
        return {"available": False, "reason": "empty smartctl output"}
    model = data.get("model_name", "?")
    serial = data.get("serial_number", "?")
    capacity_bytes = data.get("user_capacity", {}).get("bytes", 0) \
        if isinstance(data.get("user_capacity"), dict) else 0
    is_nvme = bool(data.get("nvme_smart_health_information_log"))

    temp_c = None
    if isinstance(data.get("temperature"), dict):
        temp_c = data["temperature"].get("current")

    power_on_hours = None
    if isinstance(data.get("power_on_time"), dict):
        power_on_hours = data["power_on_time"].get("hours")

    reallocated = pending = wearout_pct = None
    data_units_written_tb = None
    critical_flags = None

    if is_nvme:
        nvme = data["nvme_smart_health_information_log"]
        # NVMe : 'percentage_used' counter — 0=new, 100=spec end-of-life
        pu = nvme.get("percentage_used")
        if pu is not None:
            wearout_pct = max(0, 100 - int(pu))
        duw = nvme.get("data_units_written")  # in units of 512000 bytes
        if duw is not None:
            data_units_written_tb = round(int(duw) * 512000 / 1e12, 2)
        critical_flags = nvme.get("critical_warning")
    else:
        # SATA : SMART attribute table
        attrs = data.get("ata_smart_attributes", {}).get("table", []) \
            if isinstance(data.get("ata_smart_attributes"), dict) else []
        reallocated = _attr_value(attrs, "Reallocated_Sector_Ct")
        pending = _attr_value(attrs, "Current_Pending_Sector")
        # SSD wear leveling — multiple attribute names depending on vendor
        for name in ("Wear_Leveling_Count", "SSD_Life_Left", "Media_Wearout_Indicator",
                     "Percent_Lifetime_Remain"):
            v = _attr_value(attrs, name)
            if v is not None and 0 <= v <= 100:
                wearout_pct = v
                break

    verdict = _verdict(temp_c, reallocated, pending, wearout_pct, critical_flags)
    return {
        "available": True,
        "model": model,
        "serial": serial[:32] if serial else "?",
        "capacity_gb": round(capacity_bytes / 1e9, 1) if capacity_bytes else None,
        "is_nvme": is_nvme,
        "temp_c": temp_c,
        "power_on_hours": power_on_hours,
        "reallocated_sectors": reallocated,
        "pending_sectors": pending,
        "wearout_pct": wearout_pct,
        "data_units_written_tb": data_units_written_tb,
        "critical_warning_flags": critical_flags,
        "verdict": verdict,
    }


def _verdict(temp_c, reallocated, pending, wearout_pct, critical_flags) -> dict:
    """Aggregate the per-attribute signals into ok/warn/fail."""
    reasons: list = []
    kind = "ok"
    if critical_flags is not None and critical_flags > 0:
        reasons.append(f"NVMe critical_warning={critical_flags}")
        kind = "fail"
    if pending is not None and pending > 0:
        reasons.append(f"{pending} pending sectors")
        if kind != "fail":
            kind = "warn"
    if reallocated is not None and reallocated > 0:
        reasons.append(f"{reallocated} reallocated sectors")
        if kind != "fail":
            kind = "warn"
    if wearout_pct is not None:
        if wearout_pct < 5:
            reasons.append(f"wearout {wearout_pct}% (end-of-life)")
            kind = "fail"
        elif wearout_pct < 20 and kind != "fail":
            reasons.append(f"wearout {wearout_pct}% (low)")
            kind = "warn"
    if temp_c is not None and temp_c > 70 and kind != "fail":
        reasons.append(f"temp {temp_c}°C (hot)")
        kind = "warn"
    return {"kind": kind, "reasons": reasons or ["all clear"]}


def status() -> dict:
    """Top-level audit. Iterates all detected devices."""
    if not has_smartctl():
        return {"ok": True, "available": False, "reason": "smartctl not installed"}
    devices = list_devices()
    if not devices:
        return {"ok": True, "available": False, "reason": "no /dev/sd* or /dev/nvmeXnY found"}
    disks: list = []
    worst_kind = "ok"
    rank = {"ok": 0, "warn": 1, "fail": 2}
    for dev in devices:
        raw = run_smartctl(dev)
        if raw is None:
            disks.append({"device": dev, "available": False, "reason": "smartctl failed"})
            continue
        parsed = parse_smart(raw)
        parsed["device"] = dev
        disks.append(parsed)
        v = parsed.get("verdict", {}).get("kind", "ok")
        if rank.get(v, 0) > rank.get(worst_kind, 0):
            worst_kind = v
    return {
        "ok": True,
        "available": True,
        "device_count": len(devices),
        "worst_verdict": worst_kind,
        "disks": disks,
    }

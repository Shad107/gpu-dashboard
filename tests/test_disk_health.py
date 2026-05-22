"""R&D #12.2 — SMART disk health correlator tests."""
import json
import pytest
import subprocess
from unittest.mock import patch
from gpu_dashboard.modules import disk_health as dh


class FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


# Sample smartctl outputs (real shape) ────────────────────────────────────


NVME_OUTPUT = json.dumps({
    "model_name": "Samsung SSD 980 PRO 1TB",
    "serial_number": "S5GXNX0R123456",
    "user_capacity": {"bytes": 1000204886016},
    "temperature": {"current": 42},
    "power_on_time": {"hours": 1500},
    "nvme_smart_health_information_log": {
        "percentage_used": 8,
        "data_units_written": 200000000,  # 200M units × 512000 = ~100 TB
        "critical_warning": 0,
    },
})

SATA_OUTPUT = json.dumps({
    "model_name": "Crucial MX500 1TB",
    "serial_number": "1234ABCD",
    "user_capacity": {"bytes": 1000204886016},
    "temperature": {"current": 38},
    "power_on_time": {"hours": 8000},
    "ata_smart_attributes": {
        "table": [
            {"id": 5,   "name": "Reallocated_Sector_Ct", "value": 100, "raw": {"value": 0}},
            {"id": 197, "name": "Current_Pending_Sector", "value": 100, "raw": {"value": 0}},
            {"id": 173, "name": "Wear_Leveling_Count",   "value": 85,  "raw": {"value": 85}},
        ],
    },
})


# ── list_devices ────────────────────────────────────────────────────────


def test_list_devices_filters_partitions():
    with patch("os.listdir", return_value=["sda", "sda1", "sda2", "sdb", "nvme0n1", "nvme0n1p1"]):
        devs = dh.list_devices()
    # Should keep sda + sdb + nvme0n1 ; drop sda1/sda2/nvme0n1p1
    assert "/dev/sda" in devs
    assert "/dev/sdb" in devs
    assert "/dev/nvme0n1" in devs
    assert "/dev/sda1" not in devs
    assert "/dev/nvme0n1p1" not in devs


def test_list_devices_handles_missing_dev():
    with patch("os.listdir", side_effect=OSError):
        assert dh.list_devices() == []


# ── run_smartctl ────────────────────────────────────────────────────────


def test_run_smartctl_returns_none_when_missing():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        assert dh.run_smartctl("/dev/sda") is None


def test_run_smartctl_parses_json():
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=NVME_OUTPUT)):
        d = dh.run_smartctl("/dev/nvme0n1")
    assert d is not None
    assert d["model_name"] == "Samsung SSD 980 PRO 1TB"


def test_run_smartctl_handles_bad_json():
    with patch.object(subprocess, "run", return_value=FakeProc(stdout="not json")):
        assert dh.run_smartctl("/dev/sda") is None


# ── parse_smart : NVMe ──────────────────────────────────────────────────


def test_parse_nvme_basic():
    p = dh.parse_smart(json.loads(NVME_OUTPUT))
    assert p["available"] is True
    assert p["is_nvme"] is True
    assert p["model"] == "Samsung SSD 980 PRO 1TB"
    assert p["temp_c"] == 42
    assert p["power_on_hours"] == 1500
    # percentage_used=8 → wearout_pct = 92
    assert p["wearout_pct"] == 92
    # 200M units × 512000 bytes / 1e12 = 102.4 TB
    assert p["data_units_written_tb"] == 102.4
    assert p["verdict"]["kind"] == "ok"


def test_parse_nvme_critical_warning_fails():
    raw = json.loads(NVME_OUTPUT)
    raw["nvme_smart_health_information_log"]["critical_warning"] = 2
    p = dh.parse_smart(raw)
    assert p["verdict"]["kind"] == "fail"
    assert any("critical_warning" in r for r in p["verdict"]["reasons"])


def test_parse_nvme_wearout_low_warns():
    raw = json.loads(NVME_OUTPUT)
    raw["nvme_smart_health_information_log"]["percentage_used"] = 85  # = 15% left
    p = dh.parse_smart(raw)
    assert p["wearout_pct"] == 15
    assert p["verdict"]["kind"] == "warn"


def test_parse_nvme_wearout_eol_fails():
    raw = json.loads(NVME_OUTPUT)
    raw["nvme_smart_health_information_log"]["percentage_used"] = 98
    p = dh.parse_smart(raw)
    assert p["wearout_pct"] == 2
    assert p["verdict"]["kind"] == "fail"


# ── parse_smart : SATA ──────────────────────────────────────────────────


def test_parse_sata_basic():
    p = dh.parse_smart(json.loads(SATA_OUTPUT))
    assert p["available"] is True
    assert p["is_nvme"] is False
    assert p["model"] == "Crucial MX500 1TB"
    assert p["temp_c"] == 38
    assert p["reallocated_sectors"] == 0
    assert p["pending_sectors"] == 0
    assert p["wearout_pct"] == 85
    assert p["verdict"]["kind"] == "ok"


def test_parse_sata_pending_sectors_warns():
    raw = json.loads(SATA_OUTPUT)
    raw["ata_smart_attributes"]["table"][1]["raw"]["value"] = 3
    p = dh.parse_smart(raw)
    assert p["pending_sectors"] == 3
    assert p["verdict"]["kind"] == "warn"
    assert any("pending" in r for r in p["verdict"]["reasons"])


def test_parse_sata_hot_temp_warns():
    raw = json.loads(SATA_OUTPUT)
    raw["temperature"]["current"] = 75
    p = dh.parse_smart(raw)
    assert p["verdict"]["kind"] == "warn"
    assert any("hot" in r for r in p["verdict"]["reasons"])


def test_parse_empty_input():
    assert dh.parse_smart({}).get("available") is True  # technically valid empty dict
    p = dh.parse_smart(None)
    assert p["available"] is False


# ── status() ────────────────────────────────────────────────────────────


def test_status_no_smartctl():
    with patch.object(dh, "has_smartctl", return_value=False):
        s = dh.status()
    assert s["available"] is False
    assert "smartctl not installed" in s["reason"]


def test_status_no_devices():
    with patch.object(dh, "has_smartctl", return_value=True), \
         patch.object(dh, "list_devices", return_value=[]):
        s = dh.status()
    assert s["available"] is False


def test_status_aggregates_devices():
    with patch.object(dh, "has_smartctl", return_value=True), \
         patch.object(dh, "list_devices", return_value=["/dev/nvme0n1", "/dev/sda"]), \
         patch.object(dh, "run_smartctl",
                      side_effect=[json.loads(NVME_OUTPUT), json.loads(SATA_OUTPUT)]):
        s = dh.status()
    assert s["device_count"] == 2
    assert len(s["disks"]) == 2
    assert s["worst_verdict"] == "ok"


def test_status_worst_verdict_propagates():
    """If one disk fails, worst_verdict reflects it."""
    bad_nvme = json.loads(NVME_OUTPUT)
    bad_nvme["nvme_smart_health_information_log"]["critical_warning"] = 1
    with patch.object(dh, "has_smartctl", return_value=True), \
         patch.object(dh, "list_devices", return_value=["/dev/nvme0n1", "/dev/sda"]), \
         patch.object(dh, "run_smartctl",
                      side_effect=[bad_nvme, json.loads(SATA_OUTPUT)]):
        s = dh.status()
    assert s["worst_verdict"] == "fail"

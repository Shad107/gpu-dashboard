"""R&D #28.5 — thermal-zone correlator tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import thermal_zones as tz


# ── list_zones ─────────────────────────────────────────────────────────


def test_list_zones(tmp_path):
    (tmp_path / "thermal_zone0").mkdir()
    (tmp_path / "thermal_zone1").mkdir()
    (tmp_path / "cooling_device0").mkdir()  # skip
    out = tz.list_zones(root=str(tmp_path))
    assert len(out) == 2


def test_list_zones_empty(tmp_path):
    assert tz.list_zones(root=str(tmp_path)) == []


def test_list_zones_missing_root():
    assert tz.list_zones(root="/nonexistent") == []


# ── read_zone ──────────────────────────────────────────────────────────


def test_read_zone_basic(tmp_path):
    z = tmp_path / "thermal_zone0"; z.mkdir()
    (z / "type").write_text("x86_pkg_temp\n")
    (z / "temp").write_text("45000\n")
    out = tz.read_zone(str(z))
    assert out["type"] == "x86_pkg_temp"
    assert out["temp_c"] == 45.0
    assert out["temp_mc"] == 45000


def test_read_zone_missing(tmp_path):
    z = tmp_path / "thermal_zone99"; z.mkdir()
    assert tz.read_zone(str(z)) is None


def test_read_zone_bad_temp(tmp_path):
    z = tmp_path / "thermal_zone0"; z.mkdir()
    (z / "type").write_text("x86\n")
    (z / "temp").write_text("garbage\n")
    assert tz.read_zone(str(z)) is None


# ── classify_zone ──────────────────────────────────────────────────────


def test_classify_cool():
    assert tz.classify_zone(50_000) == "cool"


def test_classify_warm():
    assert tz.classify_zone(70_000) == "warm"


def test_classify_hot():
    assert tz.classify_zone(80_000) == "hot"


def test_classify_critical():
    assert tz.classify_zone(95_000) == "critical"


# ── is_storage_zone / is_cpu_zone ──────────────────────────────────────


def test_is_storage_nvme():
    assert tz.is_storage_zone("nvme0_composite") is True


def test_is_storage_ssd():
    assert tz.is_storage_zone("ssd_t1") is True


def test_is_storage_no():
    assert tz.is_storage_zone("x86_pkg_temp") is False


def test_is_cpu_zone_intel():
    assert tz.is_cpu_zone("x86_pkg_temp") is True
    assert tz.is_cpu_zone("coretemp_core0") is True


def test_is_cpu_zone_amd():
    assert tz.is_cpu_zone("k10temp") is True


def test_is_cpu_zone_arm():
    assert tz.is_cpu_zone("cpu_thermal") is True


def test_is_cpu_zone_no():
    assert tz.is_cpu_zone("nvme") is False


# ── cross_correlate ────────────────────────────────────────────────────


def test_correlate_no_gpu_throttle():
    zones = [{"type": "nvme0_composite", "temp_c": 80, "category": "hot"}]
    assert tz.cross_correlate(zones, gpu_throttled=False) == []


def test_correlate_nvme_advice():
    zones = [{"type": "nvme0_composite", "temp_c": 80, "category": "hot"}]
    out = tz.cross_correlate(zones, gpu_throttled=True)
    assert len(out) == 1
    assert "NVMe" in out[0] or "SSD" in out[0]
    assert "80" in out[0]


def test_correlate_cpu_advice():
    zones = [{"type": "x86_pkg_temp", "temp_c": 87, "category": "critical"}]
    out = tz.cross_correlate(zones, gpu_throttled=True)
    assert "CPU" in out[0]


def test_correlate_only_hot_zones():
    zones = [
        {"type": "nvme0", "temp_c": 50, "category": "cool"},
        {"type": "nvme1", "temp_c": 82, "category": "hot"},
    ]
    out = tz.cross_correlate(zones, gpu_throttled=True)
    assert len(out) == 1
    assert "82" in out[0]


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_zones():
    with patch.object(tz, "list_zones", return_value=[]):
        s = tz.status()
    assert s["zone_count"] == 0
    assert "VM" in s["summary"] or "no thermal" in s["summary"].lower()


def test_status_with_zones_no_throttle(tmp_path):
    with patch.object(tz, "list_zones", return_value=["/sys/.../zone0"]):
        with patch.object(tz, "read_zone",
                          return_value={"name": "thermal_zone0",
                                          "type": "x86_pkg_temp",
                                          "temp_mc": 55000, "temp_c": 55.0}):
            # Avoid pulling real throttle_bits sibling
            with patch.dict("sys.modules",
                              {"gpu_dashboard.modules.throttle_bits": None}):
                s = tz.status()
    assert s["zone_count"] == 1
    assert s["category_counts"]["cool"] == 1


def test_status_aggregates_categories():
    fake_zones = [
        {"name": "z0", "type": "x86_pkg", "temp_mc": 55000, "temp_c": 55.0},
        {"name": "z1", "type": "nvme0", "temp_mc": 82000, "temp_c": 82.0},
        {"name": "z2", "type": "ssd", "temp_mc": 95000, "temp_c": 95.0},
    ]
    with patch.object(tz, "list_zones",
                       return_value=["a", "b", "c"]):
        with patch.object(tz, "read_zone", side_effect=fake_zones):
            with patch.dict("sys.modules",
                              {"gpu_dashboard.modules.throttle_bits": None}):
                s = tz.status()
    cats = s["category_counts"]
    assert cats["cool"] == 1
    assert cats["hot"] == 1
    assert cats["critical"] == 1

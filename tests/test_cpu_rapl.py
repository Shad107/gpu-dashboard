"""R&D #27.3 — CPU-package RAPL harvester tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import cpu_rapl as cr


# ── list_rapl_packages ─────────────────────────────────────────────────


def test_list_packages(tmp_path):
    (tmp_path / "intel-rapl:0").mkdir()
    (tmp_path / "intel-rapl:1").mkdir()
    (tmp_path / "intel-rapl:0:0").mkdir()  # subdomain — skip
    out = cr.list_rapl_packages(root=str(tmp_path))
    assert len(out) == 2
    assert all("intel-rapl:0:0" not in p for p in out)


def test_list_packages_empty(tmp_path):
    assert cr.list_rapl_packages(root=str(tmp_path)) == []


def test_list_packages_no_root():
    assert cr.list_rapl_packages(root="/nonexistent") == []


# ── read_energy_uj ─────────────────────────────────────────────────────


def test_read_energy_present(tmp_path):
    pkg = tmp_path / "intel-rapl:0"; pkg.mkdir()
    (pkg / "energy_uj").write_text("12345678\n")
    assert cr.read_energy_uj(str(pkg)) == 12345678


def test_read_energy_missing(tmp_path):
    pkg = tmp_path / "intel-rapl:0"; pkg.mkdir()
    assert cr.read_energy_uj(str(pkg)) is None


def test_read_energy_garbage(tmp_path):
    pkg = tmp_path / "intel-rapl:0"; pkg.mkdir()
    (pkg / "energy_uj").write_text("abc\n")
    assert cr.read_energy_uj(str(pkg)) is None


# ── compute_watts ──────────────────────────────────────────────────────


def test_compute_watts_simple():
    """1 J in 1 s = 1 W. (1_000_000 µJ / 1 s) / 1e6 = 1 W."""
    assert cr.compute_watts(0, 1_000_000, 1.0, max_energy_uj=10_000_000) == 1.0


def test_compute_watts_high_power():
    """100 W draw over 0.5 s = 50 J = 50_000_000 µJ."""
    w = cr.compute_watts(0, 50_000_000, 0.5, max_energy_uj=10**12)
    assert abs(w - 100.0) < 0.001


def test_compute_watts_wrap():
    """Counter wraps. e1 < e0 → use (max - e0) + e1."""
    w = cr.compute_watts(e0=9_000_000_000, e1=500_000,
                          dt_s=1.0,
                          max_energy_uj=10_000_000_000)
    # delta = (10e9 - 9e9) + 500_000 = 1_000_500_000 µJ → 1000.5 W (1 s)
    assert abs(w - 1000.5) < 0.1


def test_compute_watts_negative_no_max():
    """If max not provided and delta negative, return None."""
    assert cr.compute_watts(100, 50, 1.0, max_energy_uj=None) is None


def test_compute_watts_zero_dt():
    assert cr.compute_watts(0, 100, 0, max_energy_uj=10**10) is None


# ── sample_package ─────────────────────────────────────────────────────


def test_sample_package_basic(tmp_path, monkeypatch):
    pkg = tmp_path / "intel-rapl:0"; pkg.mkdir()
    (pkg / "name").write_text("package-0\n")
    (pkg / "max_energy_range_uj").write_text("10000000000\n")
    # Write the initial value, then mutate before second read
    energies = iter([1_000_000, 51_000_000])  # 50 J delta
    real_open = open
    def fake_open(p, *a, **k):
        if p.endswith("energy_uj"):
            from io import StringIO
            return StringIO(f"{next(energies)}\n")
        return real_open(p, *a, **k)
    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr(cr.time, "sleep", lambda x: None)
    monkeypatch.setattr(cr.time, "time", iter([1000.0, 1000.5]).__next__)
    s = cr.sample_package(str(pkg))
    assert s["name"] == "package-0"
    # 50 J over 0.5 s = 100 W
    assert s["watts"] is not None
    assert abs(s["watts"] - 100.0) < 0.1


def test_sample_package_read_fails(tmp_path):
    pkg = tmp_path / "intel-rapl:0"; pkg.mkdir()
    (pkg / "name").write_text("package-0\n")
    s = cr.sample_package(str(pkg), interval_s=0.01)
    assert s["watts"] is None
    assert "error" in s


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_packages():
    with patch.object(cr, "list_rapl_packages", return_value=[]):
        s = cr.status()
    assert s["ok"] is False
    assert s["supported"] is False
    assert "powercap" in s["reason"].lower()


def test_status_with_packages():
    fake_samples = [
        {"name": "package-0", "watts": 65.5},
        {"name": "package-1", "watts": 50.0},
    ]
    with patch.object(cr, "list_rapl_packages",
                       return_value=["/sys/.../rapl:0", "/sys/.../rapl:1"]):
        with patch.object(cr, "sample_package", side_effect=fake_samples):
            s = cr.status()
    assert s["total_watts"] == 115.5
    assert len(s["samples"]) == 2


def test_status_handles_partial_failure():
    """One package reads OK, the other fails."""
    fake_samples = [
        {"name": "package-0", "watts": 65.0},
        {"name": "package-1", "watts": None, "error": "read failed"},
    ]
    with patch.object(cr, "list_rapl_packages",
                       return_value=["/sys/.../rapl:0", "/sys/.../rapl:1"]):
        with patch.object(cr, "sample_package", side_effect=fake_samples):
            s = cr.status()
    assert s["total_watts"] == 65.0


def test_status_all_failed():
    with patch.object(cr, "list_rapl_packages",
                       return_value=["/sys/.../rapl:0"]):
        with patch.object(cr, "sample_package",
                          return_value={"name": "p0", "watts": None,
                                         "error": "x"}):
            s = cr.status()
    assert s["total_watts"] is None


def test_status_uses_cfg_interval():
    with patch.object(cr, "list_rapl_packages", return_value=[]):
        # No packages → skip sample call ; just verify config parsing path
        s = cr.status(cfg={"CPU_RAPL_INTERVAL_S": "1.5"})
    assert s["ok"] is False

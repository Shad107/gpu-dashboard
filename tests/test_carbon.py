"""R&D #13.4 — carbon-intensity overlay tests."""
import csv
import os
import tempfile
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import carbon


def _write_csv(path, rows):
    with open(path, "w") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow(r)


@pytest.fixture(autouse=True)
def _reset_cache():
    """The module caches loads — reset between tests."""
    carbon._cache["by_hour"] = None
    carbon._cache["loaded_ts"] = 0
    yield


# ── load_csv ─────────────────────────────────────────────────────────────


def test_load_csv_missing_returns_empty():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(carbon, "csv_path",
                          return_value=os.path.join(td, "nope.csv")):
            assert carbon.load_csv(force=True) == {}


def test_load_csv_parses_24_hour_rows():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "grid.csv")
        _write_csv(p, [(0, 45), (1, 42), (12, 80), (23, 68)])
        with patch.object(carbon, "csv_path", return_value=p):
            d = carbon.load_csv(force=True)
    assert d[0] == 45.0
    assert d[12] == 80.0
    assert d[23] == 68.0


def test_load_csv_skips_comment_and_invalid_rows():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "grid.csv")
        with open(p, "w") as f:
            f.write("# this is a comment\n0,45\nNaN,99\n5,30\n")
        with patch.object(carbon, "csv_path", return_value=p):
            d = carbon.load_csv(force=True)
    assert d == {0: 45.0, 5: 30.0}


def test_load_csv_rejects_invalid_hour():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "grid.csv")
        _write_csv(p, [(24, 100), (5, 30)])  # 24 is out of range
        with patch.object(carbon, "csv_path", return_value=p):
            d = carbon.load_csv(force=True)
    assert d == {5: 30.0}


# ── current_intensity ────────────────────────────────────────────────────


def test_current_intensity_no_csv_returns_none():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(carbon, "csv_path",
                          return_value=os.path.join(td, "missing.csv")):
            assert carbon.current_intensity(now_hour=12) is None


def test_current_intensity_exact_hour():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "grid.csv")
        _write_csv(p, [(0, 45), (1, 42), (12, 80)])
        with patch.object(carbon, "csv_path", return_value=p):
            assert carbon.current_intensity(now_hour=12) == 80.0


def test_current_intensity_missing_hour_falls_back_to_avg():
    """If the CSV doesn't have entry for current hour, return the avg."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "grid.csv")
        _write_csv(p, [(0, 40), (12, 80)])
        with patch.object(carbon, "csv_path", return_value=p):
            # asking for hour=5 → average = (40+80)/2 = 60
            assert carbon.current_intensity(now_hour=5) == 60.0


# ── gco2_per_token ───────────────────────────────────────────────────────


def test_gco2_per_token_typical_case():
    """At 250W, 30 tok/s, 50 gCO2/kWh : per-token = 250*50/(30*3.6e6) ≈ 0.000116g."""
    g = carbon.gco2_per_token(250, 30, 50)
    assert g is not None
    assert 1e-4 < g < 1.5e-4


def test_gco2_per_token_zero_tps_returns_none():
    assert carbon.gco2_per_token(250, 0, 50) is None


def test_gco2_per_token_negative_inputs_return_none():
    assert carbon.gco2_per_token(-10, 30, 50) is None
    assert carbon.gco2_per_token(250, 30, -1) is None


# ── gco2_for_kwh ─────────────────────────────────────────────────────────


def test_gco2_for_kwh_basic():
    # 2 kWh at 60 gCO2/kWh = 120 g
    assert carbon.gco2_for_kwh(2.0, 60) == 120.0


def test_gco2_for_kwh_negative_kwh_clamped_to_zero():
    assert carbon.gco2_for_kwh(-1.0, 60) == 0.0


# ── status() ─────────────────────────────────────────────────────────────


def test_status_no_csv_returns_unavailable():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(carbon, "csv_path",
                          return_value=os.path.join(td, "missing.csv")):
            s = carbon.status()
    assert s["available"] is False
    assert "csv" in s["reason"].lower()


def test_status_with_csv_includes_intensity_and_aggregates():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "grid.csv")
        _write_csv(p, [(h, 50 + h * 2) for h in range(24)])
        with patch.object(carbon, "csv_path", return_value=p):
            s = carbon.status()
    assert s["available"] is True
    assert "current_gco2_per_kwh" in s
    assert s["day_min_gco2_per_kwh"] == 50.0
    assert s["day_max_gco2_per_kwh"] == 96.0


def test_status_includes_kwh_today_when_provided():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "grid.csv")
        _write_csv(p, [(h, 50) for h in range(24)])
        with patch.object(carbon, "csv_path", return_value=p):
            s = carbon.status(kwh_today=2.0)
    assert s["gco2_today_g"] == 100.0  # 2 * 50


def test_status_includes_per_token_when_watts_tps_provided():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "grid.csv")
        _write_csv(p, [(h, 50) for h in range(24)])
        with patch.object(carbon, "csv_path", return_value=p):
            s = carbon.status(watts_now=250, tps_now=30)
    assert "gco2_per_token_g" in s
    assert s["gco2_per_token_g"] > 0

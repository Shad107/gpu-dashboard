"""R&D #15.2 — tariff-aware scheduler tests."""
import csv
import os
import tempfile
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import tariff


@pytest.fixture(autouse=True)
def _reset_cache():
    tariff._cache["by_hour"] = None
    tariff._cache["loaded_ts"] = 0
    yield


def _write_csv(path, rows):
    with open(path, "w") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow(r)


def _peak_csv(path):
    """24-hour CSV : cheap nights, expensive 17h-21h."""
    rows = []
    for h in range(24):
        if 17 <= h <= 21:
            rows.append((h, 0.27))
        elif 22 <= h or h <= 6:
            rows.append((h, 0.13))
        else:
            rows.append((h, 0.20))
    _write_csv(path, rows)


# ── load_csv ─────────────────────────────────────────────────────────────


def test_load_csv_missing_returns_empty():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(tariff, "csv_path",
                          return_value=os.path.join(td, "missing.csv")):
            assert tariff.load_csv(force=True) == {}


def test_load_csv_parses_24_hour_rows():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "tariffs.csv")
        _peak_csv(p)
        with patch.object(tariff, "csv_path", return_value=p):
            d = tariff.load_csv(force=True)
    assert len(d) == 24
    assert d[19] == 0.27
    assert d[3] == 0.13


def test_load_csv_skips_comments():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "tariffs.csv")
        with open(p, "w") as f:
            f.write("# comment line\n0,0.10\n5,0.15\n")
        with patch.object(tariff, "csv_path", return_value=p):
            d = tariff.load_csv(force=True)
    assert d == {0: 0.10, 5: 0.15}


# ── current_rate ─────────────────────────────────────────────────────────


def test_current_rate_no_csv_returns_none():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(tariff, "csv_path",
                          return_value=os.path.join(td, "missing.csv")):
            assert tariff.current_rate(now_hour=12) is None


def test_current_rate_known_hour():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "tariffs.csv")
        _peak_csv(p)
        with patch.object(tariff, "csv_path", return_value=p):
            assert tariff.current_rate(now_hour=19) == 0.27
            assert tariff.current_rate(now_hour=3) == 0.13


def test_current_rate_unknown_hour_falls_back_to_avg():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "tariffs.csv")
        _write_csv(p, [(0, 0.10), (12, 0.30)])
        with patch.object(tariff, "csv_path", return_value=p):
            # hour 5 not in CSV → average = 0.20
            assert tariff.current_rate(now_hour=5) == 0.20


# ── estimate_job_cost ────────────────────────────────────────────────────


def test_estimate_returns_none_without_csv():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(tariff, "csv_path",
                          return_value=os.path.join(td, "missing.csv")):
            assert tariff.estimate_job_cost(250, 3600) is None


def test_estimate_single_hour_job():
    """250 W × 1 h at 0.20 €/kWh = 0.250 kWh × 0.20 = 0.050 €."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "tariffs.csv")
        _write_csv(p, [(h, 0.20) for h in range(24)])
        with patch.object(tariff, "csv_path", return_value=p):
            est = tariff.estimate_job_cost(250, 3600, start_hour=10)
    assert est is not None
    assert abs(est["kwh"] - 0.25) < 1e-6
    assert abs(est["cost_eur"] - 0.05) < 1e-6


def test_estimate_crosses_hour_boundaries():
    """500 W × 2 h spanning 17h (peak 0.30) + 18h (peak 0.30) = 0.300 €."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "tariffs.csv")
        _write_csv(p, [(17, 0.30), (18, 0.30)])
        with patch.object(tariff, "csv_path", return_value=p):
            est = tariff.estimate_job_cost(500, 7200, start_hour=17)
    # 1 kWh × 0.30 = 0.30 €
    assert abs(est["cost_eur"] - 0.30) < 1e-3
    # Spans 2 hours → 2 breakdown rows
    assert len(est["hours_breakdown"]) == 2


def test_estimate_includes_per_hour_breakdown():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "tariffs.csv")
        _peak_csv(p)
        with patch.object(tariff, "csv_path", return_value=p):
            est = tariff.estimate_job_cost(250, 3600 * 5, start_hour=15)
    assert len(est["hours_breakdown"]) == 5
    # hour 15 = 0.20, 16 = 0.20, 17 = 0.27 (peak), 18 = 0.27, 19 = 0.27
    rates = [b["rate_eur_per_kwh"] for b in est["hours_breakdown"]]
    assert rates == [0.20, 0.20, 0.27, 0.27, 0.27]


# ── find_cheapest_start ─────────────────────────────────────────────────


def test_cheapest_start_picks_offpeak():
    """For an 8h job, the cheapest start should land entirely in off-peak."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "tariffs.csv")
        _peak_csv(p)
        with patch.object(tariff, "csv_path", return_value=p):
            # 8h at 200 W
            r = tariff.find_cheapest_start(watts_avg=200, duration_s=8 * 3600,
                                            within_h=24)
    assert r is not None
    assert r["best"]["start_hour"] in (22, 23) or r["best"]["start_hour"] <= 6
    assert r["best"]["cost_eur"] < r["worst_for_comparison"]["cost_eur"]


def test_cheapest_start_includes_savings():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "tariffs.csv")
        _peak_csv(p)
        with patch.object(tariff, "csv_path", return_value=p):
            r = tariff.find_cheapest_start(200, 3 * 3600, within_h=24)
    assert "absolute_savings_eur" in r
    assert "savings_pct" in r
    assert r["absolute_savings_eur"] >= 0
    assert 0 <= r["savings_pct"] <= 100


def test_cheapest_start_no_csv():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(tariff, "csv_path",
                          return_value=os.path.join(td, "missing.csv")):
            assert tariff.find_cheapest_start(200, 3600) is None


# ── status ───────────────────────────────────────────────────────────────


def test_status_unavailable_when_no_csv():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(tariff, "csv_path",
                          return_value=os.path.join(td, "missing.csv")):
            s = tariff.status()
    assert s["available"] is False


def test_status_aggregates_when_csv_present():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "tariffs.csv")
        _peak_csv(p)
        with patch.object(tariff, "csv_path", return_value=p):
            s = tariff.status()
    assert s["available"] is True
    assert s["day_min_eur_per_kwh"] == 0.13
    assert s["day_max_eur_per_kwh"] == 0.27
    # Peak hours include 17-21
    assert 19 in s["peak_hours"]
    # Cheapest include 22-06
    assert 3 in s["cheapest_hours"] or 22 in s["cheapest_hours"]

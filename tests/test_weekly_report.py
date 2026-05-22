"""R&D #11.5 — Weekly report generation tests."""
import time
import pytest
from gpu_dashboard.modules import weekly_report as wr
from gpu_dashboard.storage import Storage
from gpu_dashboard.config import Config


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    base = int(time.time()) - 3 * 86400
    for i in range(50):
        s.record_sample({
            "ts": base + i * 60,
            "temp": 50 + (i % 30),
            "fan": 40, "fan0_rpm": 800, "fan1_rpm": 800,
            "clk_gpu": 1500, "clk_mem": 9000,
            "power": 100 + (i % 80),
            "power_limit": 350,
            "util_gpu": (i % 100),
            "mem_used_mib": 4000 + i * 10,
        })
    return s


# ── _spark_svg ────────────────────────────────────────────────────────────


def test_spark_svg_empty_returns_empty_svg():
    svg = wr._spark_svg([])
    assert "<svg" in svg
    assert "</svg>" in svg


def test_spark_svg_constant_does_not_divide_by_zero():
    """All values equal → still renders without ZeroDivisionError."""
    svg = wr._spark_svg([50, 50, 50])
    assert "polyline" in svg


def test_spark_svg_includes_polyline_points():
    svg = wr._spark_svg([10, 50, 30, 80])
    assert "polyline" in svg
    assert "points=" in svg


# ── _fmt_ago ──────────────────────────────────────────────────────────────


def test_fmt_ago_under_minute():
    assert wr._fmt_ago(30) == "30s"


def test_fmt_ago_minutes():
    assert wr._fmt_ago(125) == "2m"


def test_fmt_ago_hours():
    assert wr._fmt_ago(3 * 3600 + 60) == "3h"


def test_fmt_ago_days():
    assert wr._fmt_ago(2 * 86400 + 100) == "2d"


# ── compute_stats ─────────────────────────────────────────────────────────


def test_compute_stats_zero_samples(tmp_path):
    s = Storage(str(tmp_path / "empty.db"))
    stats = wr.compute_stats(s, days=7)
    assert stats["sample_count"] == 0


def test_compute_stats_aggregates_samples(storage):
    stats = wr.compute_stats(storage, days=7)
    assert stats["sample_count"] == 50
    assert stats["temp_max"] is not None
    assert stats["temp_avg"] is not None
    assert stats["power_max"] is not None
    assert stats["energy_wh"] > 0


def test_compute_stats_respects_days_window(storage):
    """1-day window should not see samples from 3 days ago."""
    stats = wr.compute_stats(storage, days=1)
    assert stats["sample_count"] == 0


def test_compute_stats_includes_temp_series(storage):
    stats = wr.compute_stats(storage, days=7)
    assert "temp_series" in stats
    assert len(stats["temp_series"]) <= 100  # capped at 100


# ── render_html ───────────────────────────────────────────────────────────


def test_render_html_no_samples():
    stats = {"period_days": 7, "sample_count": 0}
    out = wr.render_html(stats)
    assert "No samples" in out


def test_render_html_includes_stats(storage):
    stats = wr.compute_stats(storage, days=7)
    cfg = Config(defaults={"ELECTRICITY_PRICE_EUR_PER_KWH": "0.30",
                            "ELECTRICITY_CURRENCY": "EUR"})
    out = wr.render_html(stats, cfg)
    assert "<html>" in out
    assert "Energy" in out
    assert "kWh" in out
    assert "€" in out  # EUR currency symbol
    assert "polyline" in out  # sparkline


def test_render_html_currency_usd(storage):
    stats = wr.compute_stats(storage, days=7)
    cfg = Config(defaults={"ELECTRICITY_PRICE_EUR_PER_KWH": "0.15",
                            "ELECTRICITY_CURRENCY": "USD"})
    out = wr.render_html(stats, cfg)
    assert "$" in out


# ── render_text ──────────────────────────────────────────────────────────


def test_render_text_no_samples():
    stats = {"period_days": 7, "sample_count": 0}
    out = wr.render_text(stats)
    assert "No samples" in out
    assert "<" not in out  # no html


def test_render_text_includes_kwh(storage):
    stats = wr.compute_stats(storage, days=7)
    out = wr.render_text(stats, Config(defaults={}))
    assert "kWh" in out
    assert "Cost" in out


def test_render_text_no_html_tags(storage):
    stats = wr.compute_stats(storage, days=7)
    out = wr.render_text(stats, Config(defaults={}))
    assert "<svg" not in out
    assert "<html" not in out
    assert "<table" not in out

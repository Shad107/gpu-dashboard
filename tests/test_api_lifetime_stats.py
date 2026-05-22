"""Tests for /api/lifetime-stats (R&D #2.2, cycle 129)."""
import pytest

from gpu_dashboard import api
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    s = Storage(str(tmp_path / "metrics.db"))
    yield {"storage": s}
    s.close()


def test_no_storage_returns_503():
    code, body = api.handle_lifetime_stats({})
    assert code == 503


def test_empty_db_returns_zero_count(ctx):
    code, body = api.handle_lifetime_stats(ctx)
    assert code == 200
    assert body["ok"]
    assert body["samples_count"] == 0
    assert body["peak_temp_c"] is None
    assert body["peak_power_w"] is None
    assert body["lowest_idle_power_w"] is None


def test_peak_extrema_from_samples(ctx):
    """Plant samples with varying temp/power → query should return MAX."""
    ctx["storage"].record_sample({"ts": 100, "temp": 55, "power": 100.0, "fan_pct": 30, "util_gpu": 40})
    ctx["storage"].record_sample({"ts": 200, "temp": 78, "power": 250.0, "fan_pct": 80, "util_gpu": 95})
    ctx["storage"].record_sample({"ts": 300, "temp": 65, "power": 180.0, "fan_pct": 55, "util_gpu": 60})
    _, body = api.handle_lifetime_stats(ctx)
    assert body["peak_temp_c"] == 78
    assert body["peak_power_w"] == 250.0
    assert body["peak_fan_pct"] == 80
    assert body["samples_count"] == 3


def test_lowest_idle_power_filters_high_util(ctx):
    """Only samples with util_gpu < 5 should count toward lowest_idle."""
    # idle samples : util 1, 2, 3 with low power
    ctx["storage"].record_sample({"ts": 100, "temp": 40, "power": 12.0, "util_gpu": 2})
    ctx["storage"].record_sample({"ts": 200, "temp": 41, "power": 15.0, "util_gpu": 3})
    # high-load sample with even lower power (impossible IRL, but tests the filter)
    ctx["storage"].record_sample({"ts": 300, "temp": 75, "power": 8.0, "util_gpu": 95})
    _, body = api.handle_lifetime_stats(ctx)
    # The idle filter should exclude the 8W high-load sample
    assert body["lowest_idle_power_w"] == 12.0


def test_per_gpu_isolation(ctx):
    """Samples for gpu 0 vs gpu 1 must not pollute each other."""
    ctx["storage"].record_sample({"ts": 100, "gpu_index": 0, "temp": 90, "power": 300, "util_gpu": 95})
    ctx["storage"].record_sample({"ts": 200, "gpu_index": 1, "temp": 40, "power": 50, "util_gpu": 5})
    _, body_a = api.handle_lifetime_stats(ctx, {"gpu_index": "0"})
    _, body_b = api.handle_lifetime_stats(ctx, {"gpu_index": "1"})
    assert body_a["peak_temp_c"] == 90
    assert body_b["peak_temp_c"] == 40
    assert body_a["peak_power_w"] == 300.0
    assert body_b["peak_power_w"] == 50.0


def test_first_last_ts_present(ctx):
    ctx["storage"].record_sample({"ts": 1000, "temp": 50})
    ctx["storage"].record_sample({"ts": 2000, "temp": 55})
    ctx["storage"].record_sample({"ts": 3000, "temp": 60})
    _, body = api.handle_lifetime_stats(ctx)
    assert body["first_ts"] == 1000
    assert body["last_ts"] == 3000

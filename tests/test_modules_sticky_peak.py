"""Tests for the sticky peak alerts module (R&D #2.4, cycle 132)."""
import pytest

from gpu_dashboard.modules import sticky_peak
from gpu_dashboard.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "metrics.db"))
    yield s
    s.close()


def test_no_storage_returns_empty():
    assert sticky_peak.check_and_alert(None) == []


def test_no_thresholds_no_alert(storage):
    storage.record_sample({"ts": 100, "gpu_index": 0, "temp": 95, "power": 350})
    fired = sticky_peak.check_and_alert(storage, gpu_index=0)
    assert fired == []


def test_threshold_not_crossed_no_alert(storage):
    storage.record_sample({"ts": 100, "gpu_index": 0, "temp": 60, "power": 200})
    fired = sticky_peak.check_and_alert(
        storage, gpu_index=0, threshold_temp_c=85, threshold_power_w=300,
    )
    assert fired == []


def test_temp_crossing_fires_alert(storage):
    storage.record_sample({"ts": 100, "gpu_index": 0, "temp": 92, "power": 200})
    fired = sticky_peak.check_and_alert(
        storage, gpu_index=0, threshold_temp_c=85,
    )
    assert len(fired) == 1
    assert fired[0]["metric"] == "temp"
    assert fired[0]["threshold"] == 85
    assert fired[0]["observed"] == 92


def test_already_alerted_does_not_refire(storage):
    storage.record_sample({"ts": 100, "gpu_index": 0, "temp": 92})
    # First call : fires
    f1 = sticky_peak.check_and_alert(storage, gpu_index=0, threshold_temp_c=85)
    assert len(f1) == 1
    # Second call : no new alert (sticky)
    f2 = sticky_peak.check_and_alert(storage, gpu_index=0, threshold_temp_c=85)
    assert f2 == []


def test_different_threshold_re_alerts(storage):
    storage.record_sample({"ts": 100, "gpu_index": 0, "temp": 95})
    # First threshold = 85 fires
    sticky_peak.check_and_alert(storage, gpu_index=0, threshold_temp_c=85)
    # User lowers to 80 → still over peak → fires again for new threshold
    fired = sticky_peak.check_and_alert(storage, gpu_index=0, threshold_temp_c=80)
    assert len(fired) == 1
    assert fired[0]["threshold"] == 80


def test_power_crossing_fires(storage):
    storage.record_sample({"ts": 100, "gpu_index": 0, "power": 380, "temp": 70})
    fired = sticky_peak.check_and_alert(
        storage, gpu_index=0, threshold_power_w=350,
    )
    assert len(fired) == 1
    assert fired[0]["metric"] == "power"
    assert fired[0]["observed"] == 380


def test_both_metrics_can_fire_simultaneously(storage):
    storage.record_sample({"ts": 100, "gpu_index": 0, "temp": 92, "power": 380})
    fired = sticky_peak.check_and_alert(
        storage, gpu_index=0, threshold_temp_c=85, threshold_power_w=350,
    )
    assert len(fired) == 2
    metrics = {f["metric"] for f in fired}
    assert metrics == {"temp", "power"}


def test_per_gpu_isolation(storage):
    storage.record_sample({"ts": 100, "gpu_index": 0, "temp": 95})
    storage.record_sample({"ts": 200, "gpu_index": 1, "temp": 50})
    f0 = sticky_peak.check_and_alert(storage, gpu_index=0, threshold_temp_c=85)
    f1 = sticky_peak.check_and_alert(storage, gpu_index=1, threshold_temp_c=85)
    assert len(f0) == 1
    assert f1 == []

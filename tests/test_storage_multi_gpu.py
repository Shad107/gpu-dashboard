"""Tests for multi-GPU sample storage (schema v4)."""
import pytest

from gpu_dashboard.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "metrics.db"))
    yield s
    s.close()


def test_schema_is_v4(storage):
    assert storage.schema_version() == 4


def test_default_gpu_index_zero(storage):
    storage.record_sample({"ts": 1000, "temp": 50, "power": 100})
    rows = storage.get_samples(from_ts=0)
    assert len(rows) == 1
    assert rows[0]["gpu_index"] == 0


def test_explicit_gpu_index(storage):
    storage.record_sample({"ts": 1000, "temp": 50, "power": 100, "gpu_index": 0})
    storage.record_sample({"ts": 1000, "temp": 70, "power": 150, "gpu_index": 1})
    storage.record_sample({"ts": 2000, "temp": 60, "power": 120, "gpu_index": 0})
    rows = storage.get_samples(from_ts=0)
    # Default gpu_index=0
    assert len(rows) == 2
    assert all(r["gpu_index"] == 0 for r in rows)


def test_filter_by_gpu_index(storage):
    storage.record_sample({"ts": 1000, "temp": 50, "gpu_index": 0})
    storage.record_sample({"ts": 1000, "temp": 70, "gpu_index": 1})
    storage.record_sample({"ts": 2000, "temp": 75, "gpu_index": 1})
    rows0 = storage.get_samples(from_ts=0, gpu_index=0)
    rows1 = storage.get_samples(from_ts=0, gpu_index=1)
    assert len(rows0) == 1
    assert len(rows1) == 2
    assert rows0[0]["temp"] == 50
    assert all(r["gpu_index"] == 1 for r in rows1)


def test_all_gpus_with_negative_index(storage):
    storage.record_sample({"ts": 1000, "temp": 50, "gpu_index": 0})
    storage.record_sample({"ts": 1000, "temp": 70, "gpu_index": 1})
    storage.record_sample({"ts": 2000, "temp": 80, "gpu_index": 2})
    rows = storage.get_samples(from_ts=0, gpu_index=-1)
    assert len(rows) == 3


def test_composite_primary_key_allows_same_ts_different_gpu(storage):
    """Two GPUs sampled at the exact same epoch shouldn't collide."""
    storage.record_sample({"ts": 1000, "temp": 50, "gpu_index": 0})
    storage.record_sample({"ts": 1000, "temp": 70, "gpu_index": 1})
    rows = storage.get_samples(from_ts=0, gpu_index=-1)
    assert len(rows) == 2

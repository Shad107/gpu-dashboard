"""Tests for the multi-GPU sampler (cycle 87)."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from gpu_dashboard.metrics import MetricsSampler
from gpu_dashboard.storage import Storage


def _mock_run(stdout: str, returncode: int = 0):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    return m


def test_poll_all_single_gpu():
    """Single nvidia-smi line → one sample with gpu_index=0."""
    s = MetricsSampler()
    csv = "50, 60, 1500, 9500, 100, 250, 80, 18000"
    with patch("subprocess.run", return_value=_mock_run(csv)):
        out = s._poll_all()
    assert len(out) == 1
    assert out[0]["gpu_index"] == 0
    assert out[0]["temp"] == 50
    assert out[0]["power"] == 100.0


def test_poll_all_two_gpus():
    """Two CSV lines → two samples with indices 0 and 1."""
    s = MetricsSampler()
    csv = "50, 60, 1500, 9500, 100, 250, 80, 18000\n" \
          "65, 70, 1600, 9700, 180, 300, 95, 22000"
    with patch("subprocess.run", return_value=_mock_run(csv)):
        out = s._poll_all()
    assert len(out) == 2
    assert out[0]["gpu_index"] == 0
    assert out[1]["gpu_index"] == 1
    assert out[0]["temp"] == 50
    assert out[1]["temp"] == 65


def test_poll_all_three_gpus():
    s = MetricsSampler()
    csv = "\n".join([
        "50, 60, 1500, 9500, 100, 250, 80, 18000",
        "65, 70, 1600, 9700, 180, 300, 95, 22000",
        "45, 55, 1400, 9400, 90,  250, 70, 16000",
    ])
    with patch("subprocess.run", return_value=_mock_run(csv)):
        out = s._poll_all()
    assert [s["gpu_index"] for s in out] == [0, 1, 2]


def test_poll_all_empty_on_failure():
    s = MetricsSampler()
    with patch("subprocess.run", return_value=_mock_run("", returncode=1)):
        out = s._poll_all()
    assert out == []


def test_poll_back_compat_returns_first():
    s = MetricsSampler()
    csv = "50, 60, 1500, 9500, 100, 250, 80, 18000\n" \
          "65, 70, 1600, 9700, 180, 300, 95, 22000"
    with patch("subprocess.run", return_value=_mock_run(csv)):
        out = s._poll()
    assert out is not None
    assert out["gpu_index"] == 0
    assert out["temp"] == 50


def test_persist_writes_gpu_index_to_storage(tmp_path):
    storage = Storage(str(tmp_path / "metrics.db"))
    s = MetricsSampler(storage=storage)
    s._persist({"gpu_index": 0, "temp": 50, "power": 100, "fan": 60})
    s._persist({"gpu_index": 1, "temp": 65, "power": 180, "fan": 70})
    rows0 = storage.get_samples(from_ts=0, gpu_index=0)
    rows1 = storage.get_samples(from_ts=0, gpu_index=1)
    assert len(rows0) == 1
    assert len(rows1) == 1
    assert rows0[0]["temp"] == 50
    assert rows1[0]["temp"] == 65
    storage.close()

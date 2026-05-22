"""Tests for the encoder/decoder/PCIe metrics fields added in cycle 120."""
from unittest.mock import patch, MagicMock

import pytest

from gpu_dashboard import api


def _smi_result(stdout: str, rc: int = 0):
    return MagicMock(returncode=rc, stdout=stdout)


def test_card_snapshot_parses_new_fields():
    """Full 16-field CSV row should fill all new fields."""
    csv = "RTX 3090,55,42,250.0,350.0,87,16384,24576,72,94.02.26.40.81,30,5,4,4,16,16"
    with patch("gpu_dashboard.api.subprocess.run", return_value=_smi_result(csv)):
        snap = api._gpu_card_snapshot(0)
    assert snap["alive"]
    assert snap["util_enc"] == 30
    assert snap["util_dec"] == 5
    assert snap["pcie_gen"] == 4
    assert snap["pcie_gen_max"] == 4
    assert snap["pcie_width"] == 16
    assert snap["pcie_width_max"] == 16


def test_card_snapshot_missing_new_fields_uses_none():
    """Older driver returning only 10 fields should leave new fields as None."""
    csv = "RTX 3090,55,42,250.0,350.0,87,16384,24576,72,94.02.26.40.81"
    with patch("gpu_dashboard.api.subprocess.run", return_value=_smi_result(csv)):
        snap = api._gpu_card_snapshot(0)
    assert snap["alive"]
    assert snap["util_enc"] is None
    assert snap["util_dec"] is None
    assert snap["pcie_gen"] is None
    assert snap["pcie_gen_max"] is None


def test_card_snapshot_handles_na_values():
    """nvidia-smi sometimes returns '[N/A]' for unsupported metrics → coerce to None."""
    csv = "RTX 3090,55,42,250.0,350.0,87,16384,24576,72,94.02.26.40.81,[N/A],[N/A],4,4,16,16"
    with patch("gpu_dashboard.api.subprocess.run", return_value=_smi_result(csv)):
        snap = api._gpu_card_snapshot(0)
    assert snap["util_enc"] is None
    assert snap["util_dec"] is None
    assert snap["pcie_gen"] == 4
    assert snap["pcie_width"] == 16


def test_card_snapshot_pcie_downgrade_detectable():
    """When current < max, the frontend can detect a downgrade situation."""
    csv = "RTX 3090,55,42,250.0,350.0,87,16384,24576,72,94.02.26.40.81,0,0,2,4,8,16"
    with patch("gpu_dashboard.api.subprocess.run", return_value=_smi_result(csv)):
        snap = api._gpu_card_snapshot(0)
    assert snap["pcie_gen"] < snap["pcie_gen_max"]
    assert snap["pcie_width"] < snap["pcie_width_max"]


def test_metrics_sampler_csv_with_extra_fields():
    """MetricsSampler should also parse the 14-field CSV row."""
    from gpu_dashboard.metrics import MetricsSampler
    # 14 fields from _NVIDIA_SMI_QUERY (no name column in this CSV)
    csv = "55,42,1800,9500,250.0,350.0,87,16384,30,5,4,4,16,16"
    sampler = MetricsSampler.__new__(MetricsSampler)
    sampler._display = None
    sampler._llm_url = ""

    with patch("gpu_dashboard.metrics.subprocess.run",
               return_value=_smi_result(csv)):
        samples = sampler._poll_all()
    assert len(samples) == 1
    s = samples[0]
    assert s["util_enc"] == 30
    assert s["util_dec"] == 5
    assert s["pcie_gen"] == 4
    assert s["pcie_width"] == 16

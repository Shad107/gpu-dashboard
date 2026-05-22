"""R&D #19.2 — Thermal throttle root-cause classifier tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import throttle_cause as tc


# ── _parse_active ──────────────────────────────────────────────────────


def test_parse_active_true():
    assert tc._parse_active("Active") is True
    assert tc._parse_active("active") is True
    assert tc._parse_active("1") is True
    assert tc._parse_active("yes") is True


def test_parse_active_false():
    assert tc._parse_active("Not Active") is False
    assert tc._parse_active("0") is False
    assert tc._parse_active("") is False


# ── classify_row ───────────────────────────────────────────────────────


def _empty_row():
    return {f: "Not Active" for f, _, _, _ in tc.THROTTLE_REASONS}


def test_no_throttle_active():
    v = tc.classify_row(_empty_row())
    assert v["severity"] == "info"
    assert v["reason"] == "no throttle active"


def test_sw_power_cap_alone():
    row = _empty_row()
    row["clocks_throttle_reasons.sw_power_cap"] = "Active"
    v = tc.classify_row(row)
    assert v["severity"] == "info"
    assert "power cap" in v["reason"].lower()
    assert "Raise power limit" in v["recommendation"]


def test_hw_thermal_critical():
    row = _empty_row()
    row["clocks_throttle_reasons.hw_thermal_slowdown"] = "Active"
    v = tc.classify_row(row)
    assert v["severity"] == "critical"
    assert "thermal" in v["reason"].lower()


def test_sw_thermal_is_warn():
    row = _empty_row()
    row["clocks_throttle_reasons.sw_thermal_slowdown"] = "Active"
    v = tc.classify_row(row)
    assert v["severity"] == "warn"


def test_hw_power_brake_critical():
    row = _empty_row()
    row["clocks_throttle_reasons.hw_power_brake_slowdown"] = "Active"
    v = tc.classify_row(row)
    assert v["severity"] == "critical"
    assert "PSU" in v["recommendation"]


def test_promote_to_highest_severity():
    """If both info AND critical are active, headline takes critical."""
    row = _empty_row()
    row["clocks_throttle_reasons.sw_power_cap"] = "Active"
    row["clocks_throttle_reasons.hw_thermal_slowdown"] = "Active"
    v = tc.classify_row(row)
    assert v["severity"] == "critical"
    assert len(v["active_flags"]) == 2


def test_active_flags_list():
    row = _empty_row()
    row["clocks_throttle_reasons.sw_power_cap"] = "Active"
    row["clocks_throttle_reasons.sync_boost"] = "Active"
    v = tc.classify_row(row)
    assert len(v["active_flags"]) == 2


# ── _to_float / _to_int ────────────────────────────────────────────────


def test_to_float_normal():
    assert tc._to_float("75.3") == 75.3


def test_to_float_na():
    assert tc._to_float("N/A") is None
    assert tc._to_float("[N/A]") is None
    assert tc._to_float("Not Supported") is None
    assert tc._to_float("") is None


def test_to_int_from_float_string():
    assert tc._to_int("1500.0") == 1500


def test_to_int_na():
    assert tc._to_int("N/A") is None


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_nvidia_smi():
    with patch.object(tc, "_nvidia_smi_query", return_value=None):
        s = tc.status()
    assert s["ok"] is False
    assert s["gpus"] == []


def test_status_one_gpu_no_throttle():
    fake_row = _empty_row()
    fake_row.update({
        "index": "0", "name": "RTX 3090",
        "temperature.gpu": "55", "clocks.current.graphics": "1800",
        "clocks.max.graphics": "1900", "power.draw": "200",
        "power.limit": "350",
    })
    with patch.object(tc, "_nvidia_smi_query", return_value=[fake_row]):
        s = tc.status()
    assert s["ok"] is True
    assert len(s["gpus"]) == 1
    g = s["gpus"][0]
    assert g["index"] == 0
    assert g["temp_c"] == 55.0
    assert g["clock_mhz"] == 1800
    assert g["verdict"]["severity"] == "info"


def test_status_with_critical_throttle():
    fake_row = _empty_row()
    fake_row.update({
        "index": "0", "name": "RTX 3090",
        "temperature.gpu": "92", "clocks.current.graphics": "900",
        "clocks.max.graphics": "1900", "power.draw": "350",
        "power.limit": "350",
    })
    fake_row["clocks_throttle_reasons.hw_thermal_slowdown"] = "Active"
    with patch.object(tc, "_nvidia_smi_query", return_value=[fake_row]):
        s = tc.status()
    assert s["any_throttling"] is True
    assert s["gpus"][0]["verdict"]["severity"] == "critical"

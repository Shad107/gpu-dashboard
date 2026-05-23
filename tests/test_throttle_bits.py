"""R&D #25.5 — Per-bit throttle reason decoder tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import throttle_bits as tb


# ── _parse_active ──────────────────────────────────────────────────────


def test_parse_active_yes():
    assert tb._parse_active("Active") is True
    assert tb._parse_active("active") is True
    assert tb._parse_active("1") is True


def test_parse_active_no():
    assert tb._parse_active("Not Active") is False
    assert tb._parse_active("0") is False
    assert tb._parse_active("") is False


# ── decode_bits ────────────────────────────────────────────────────────


def _row_with(active_fields: dict) -> dict:
    row = {f: "Not Active" for f, _, _, _ in tb.THROTTLE_BIT_TABLE}
    for k, v in active_fields.items():
        row[k] = v
    return row


def test_decode_all_inactive():
    bits = tb.decode_bits(_row_with({}))
    assert len(bits) == 9
    assert all(not b["active"] for b in bits)


def test_decode_one_active():
    row = _row_with({"clocks_throttle_reasons.sw_power_cap": "Active"})
    bits = tb.decode_bits(row)
    active = [b for b in bits if b["active"]]
    assert len(active) == 1
    assert active[0]["label"] == "SW power cap"
    assert active[0]["severity"] == "info"


def test_decode_critical_bit():
    row = _row_with({"clocks_throttle_reasons.hw_thermal_slowdown": "Active"})
    bits = tb.decode_bits(row)
    active = [b for b in bits if b["active"]]
    assert active[0]["severity"] == "critical"


def test_decode_multiple_active():
    row = _row_with({
        "clocks_throttle_reasons.sw_power_cap": "Active",
        "clocks_throttle_reasons.hw_thermal_slowdown": "Active",
    })
    bits = tb.decode_bits(row)
    active = [b for b in bits if b["active"]]
    assert len(active) == 2


# ── headline_verdict ───────────────────────────────────────────────────


def test_verdict_no_throttle():
    bits = tb.decode_bits(_row_with({}))
    v = tb.headline_verdict(bits)
    assert v["verdict"] == "no_throttle"
    assert v["severity"] == "info"


def test_verdict_picks_most_severe():
    row = _row_with({
        "clocks_throttle_reasons.sw_power_cap": "Active",
        "clocks_throttle_reasons.hw_thermal_slowdown": "Active",
    })
    bits = tb.decode_bits(row)
    v = tb.headline_verdict(bits)
    assert v["severity"] == "critical"
    assert "thermal" in v["verdict"].lower()


def test_verdict_only_info():
    row = _row_with({"clocks_throttle_reasons.display_clock_setting": "Active"})
    bits = tb.decode_bits(row)
    v = tb.headline_verdict(bits)
    assert v["severity"] == "info"
    assert "display" in v["verdict"].lower()


def test_verdict_promotes_warn_over_info():
    row = _row_with({
        "clocks_throttle_reasons.sw_power_cap": "Active",         # info
        "clocks_throttle_reasons.sw_thermal_slowdown": "Active",  # warn
    })
    bits = tb.decode_bits(row)
    v = tb.headline_verdict(bits)
    assert v["severity"] == "warn"


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_smi():
    with patch.object(tb, "_query_gpu", return_value=None):
        s = tb.status()
    assert s["ok"] is False
    assert s["gpus"] == []


def test_status_clean_gpu():
    fake_row = _row_with({})
    fake_row.update({"index": "0", "name": "RTX 3090"})
    with patch.object(tb, "_query_gpu", return_value=[fake_row]):
        s = tb.status()
    assert len(s["gpus"]) == 1
    assert s["gpus"][0]["active_count"] == 0
    assert s["any_critical"] is False
    assert s["gpus"][0]["verdict"]["verdict"] == "no_throttle"


def test_status_flag_critical():
    fake_row = _row_with({"clocks_throttle_reasons.hw_thermal_slowdown": "Active"})
    fake_row.update({"index": "0", "name": "RTX 3090"})
    with patch.object(tb, "_query_gpu", return_value=[fake_row]):
        s = tb.status()
    assert s["any_critical"] is True


def test_status_aggregates_multi_gpu():
    row0 = _row_with({})
    row0.update({"index": "0", "name": "GPU 0"})
    row1 = _row_with({"clocks_throttle_reasons.sw_power_cap": "Active"})
    row1.update({"index": "1", "name": "GPU 1"})
    with patch.object(tb, "_query_gpu", return_value=[row0, row1]):
        s = tb.status()
    assert len(s["gpus"]) == 2
    assert s["gpus"][0]["active_count"] == 0
    assert s["gpus"][1]["active_count"] == 1
    assert s["any_critical"] is False  # power cap is info-only

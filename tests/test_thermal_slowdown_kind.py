"""R&D #29.7 — HW vs SW thermal slowdown decoder tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import thermal_slowdown_kind as ts


def _row(sw=False, hw=False, gpu_temp=70.0, mem_temp=72.0,
         power=200, name="RTX 3090", index=0):
    return {
        "index": str(index), "name": name,
        "temperature.gpu": str(gpu_temp),
        "temperature.memory": str(mem_temp),
        "power.draw": str(power), "power.limit": "350",
        "clocks.current.graphics": "1500",
        "clocks_throttle_reasons.sw_thermal_slowdown":
            "Active" if sw else "Not Active",
        "clocks_throttle_reasons.hw_thermal_slowdown":
            "Active" if hw else "Not Active",
    }


# ── classify ───────────────────────────────────────────────────────────


def test_no_throttle():
    v = ts.classify(_row())
    assert v["verdict"] == "no_thermal_throttle"


def test_sw_normal_at_high_temp():
    v = ts.classify(_row(sw=True, gpu_temp=82))
    assert v["verdict"] == "sw_normal"
    assert v["severity"] == "info"


def test_sw_premature_at_low_temp():
    """SW slowdown at 65°C → fan probably failed."""
    v = ts.classify(_row(sw=True, gpu_temp=65))
    assert v["verdict"] == "sw_premature"
    assert v["severity"] == "warn"
    assert "fan" in v["reason"].lower() or "fan" in v["recommendation"].lower()


def test_hw_safety_net():
    """HW slowdown alone → 93+°C TJMax hit."""
    v = ts.classify(_row(hw=True, gpu_temp=95))
    assert v["verdict"] == "hw_safety_net"
    assert v["severity"] == "critical"
    assert "TJMax" in v["reason"]


def test_hw_and_sw_both():
    """Both bits → driver lost the race to HW safety net."""
    v = ts.classify(_row(sw=True, hw=True, gpu_temp=94))
    assert v["verdict"] == "hw_and_sw_both"
    assert v["severity"] == "critical"
    assert "Stop load" in v["recommendation"]


def test_sw_premature_70_edge():
    """70°C is the threshold."""
    v_below = ts.classify(_row(sw=True, gpu_temp=69))
    v_above = ts.classify(_row(sw=True, gpu_temp=70))
    assert v_below["verdict"] == "sw_premature"
    assert v_above["verdict"] == "sw_normal"


def test_classify_missing_temp_sw_normal():
    """If temp unreadable but SW bit active → falls through to sw_normal."""
    row = _row(sw=True)
    row["temperature.gpu"] = "N/A"
    v = ts.classify(row)
    # Temp comparison fails → not 'sw_premature' branch ; falls to sw_normal
    assert v["verdict"] == "sw_normal"


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_smi():
    with patch.object(ts, "_query_gpu", return_value=None):
        s = ts.status()
    assert s["ok"] is False


def test_status_no_throttle():
    with patch.object(ts, "_query_gpu", return_value=[_row()]):
        s = ts.status()
    assert s["any_critical"] is False
    assert s["gpus"][0]["verdict"]["verdict"] == "no_thermal_throttle"


def test_status_flags_hw_critical():
    with patch.object(ts, "_query_gpu",
                       return_value=[_row(hw=True, gpu_temp=95)]):
        s = ts.status()
    assert s["any_critical"] is True
    assert s["gpus"][0]["verdict"]["verdict"] == "hw_safety_net"


def test_status_per_gpu_data():
    with patch.object(ts, "_query_gpu",
                       return_value=[_row(gpu_temp=72, mem_temp=78,
                                            power=210)]):
        s = ts.status()
    g = s["gpus"][0]
    assert g["gpu_temp_c"] == 72.0
    assert g["mem_temp_c"] == 78.0
    assert g["power_w"] == 210.0


def test_status_multi_gpu_worst_critical():
    rows = [
        _row(index=0),                       # no throttle
        _row(index=1, hw=True, gpu_temp=96), # hw safety net
    ]
    with patch.object(ts, "_query_gpu", return_value=rows):
        s = ts.status()
    assert s["any_critical"] is True
    assert len(s["gpus"]) == 2

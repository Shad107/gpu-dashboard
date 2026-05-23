"""R&D #27.7 — applied-vs-enforced clock gap tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import clock_gap as cg


def _row(app=1500, cur=1500, max_=1900, **throttles):
    """Build a mock nvidia-smi CSV row."""
    base = {
        "index": "0", "name": "RTX 3090",
        "clocks.applications.gr": str(app) if app is not None else "[N/A]",
        "clocks.current.graphics": str(cur) if cur is not None else "[N/A]",
        "clocks.max.graphics": str(max_) if max_ is not None else "[N/A]",
    }
    for bit in ("sw_power_cap", "sw_thermal_slowdown",
                "hw_thermal_slowdown", "hw_power_brake_slowdown",
                "hw_slowdown", "sync_boost", "applications_clocks_setting"):
        base[f"clocks_throttle_reasons.{bit}"] = (
            "Active" if throttles.get(bit) else "Not Active")
    return base


# ── _to_int ────────────────────────────────────────────────────────────


def test_to_int_normal():
    assert cg._to_int("1500") == 1500


def test_to_int_float_string():
    assert cg._to_int("1500.0") == 1500


def test_to_int_na():
    assert cg._to_int("N/A") is None
    assert cg._to_int("[N/A]") is None
    assert cg._to_int("") is None


# ── _is_active ─────────────────────────────────────────────────────────


def test_is_active_true():
    assert cg._is_active("Active") is True


def test_is_active_false():
    assert cg._is_active("Not Active") is False
    assert cg._is_active("") is False


# ── _binding_throttle ──────────────────────────────────────────────────


def test_binding_none_when_all_inactive():
    assert cg._binding_throttle(_row()) is None


def test_binding_sw_power_cap():
    r = _row(sw_power_cap=True)
    assert cg._binding_throttle(r) == "sw_power_cap"


def test_binding_hw_thermal_wins_over_sw_power():
    r = _row(sw_power_cap=True, hw_thermal_slowdown=True)
    assert cg._binding_throttle(r) == "hw_thermal_slowdown"


def test_binding_hw_slowdown_wins():
    r = _row(hw_slowdown=True, sw_power_cap=True)
    assert cg._binding_throttle(r) == "hw_slowdown"


# ── classify ───────────────────────────────────────────────────────────


def test_classify_no_apps_clock():
    """clocks.applications.gr = 0 (not set) → no_apps_clock."""
    v = cg.classify(_row(app=0, cur=1500))
    assert v["verdict"] == "no_apps_clock"


def test_classify_no_apps_clock_na():
    """N/A applications.gr also counts as not set."""
    v = cg.classify(_row(app=None, cur=1500))
    assert v["verdict"] == "no_apps_clock"


def test_classify_applied_exact():
    v = cg.classify(_row(app=1500, cur=1500))
    assert v["verdict"] == "applied"
    assert v["gap_mhz"] == 0


def test_classify_applied_within_tolerance():
    v = cg.classify(_row(app=1500, cur=1497))
    assert v["verdict"] == "applied"


def test_classify_capped_by_power():
    v = cg.classify(_row(app=1800, cur=1500, sw_power_cap=True))
    assert v["verdict"] == "capped_by_power"
    assert "Power limit" in v["reason"]
    assert v["binding"] == "sw_power_cap"


def test_classify_capped_by_thermal():
    v = cg.classify(_row(app=1800, cur=1500, hw_thermal_slowdown=True))
    assert v["verdict"] == "capped_by_thermal"
    assert v["binding"] == "hw_thermal_slowdown"


def test_classify_capped_by_hw():
    v = cg.classify(_row(app=1800, cur=900, hw_slowdown=True))
    assert v["verdict"] == "capped_by_hw"


def test_classify_throttled_unknown():
    """Gap > 5 but no throttle bit set."""
    v = cg.classify(_row(app=1800, cur=1500))
    assert v["verdict"] == "throttled_unknown"


def test_classify_unreadable_current():
    v = cg.classify(_row(app=1800, cur=None))
    assert v["verdict"] == "unknown"


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_smi():
    with patch.object(cg, "_query_gpu", return_value=None):
        s = cg.status()
    assert s["ok"] is False


def test_status_applied():
    with patch.object(cg, "_query_gpu",
                      return_value=[_row(app=1500, cur=1500)]):
        s = cg.status()
    assert s["any_capped"] is False
    assert s["gpus"][0]["verdict"] == "applied"


def test_status_capped_flags_any_capped():
    with patch.object(cg, "_query_gpu",
                      return_value=[_row(app=1800, cur=1500,
                                          sw_power_cap=True)]):
        s = cg.status()
    assert s["any_capped"] is True
    assert s["gpus"][0]["verdict"] == "capped_by_power"


def test_status_multi_gpu_mixed():
    rows = [
        _row(app=1500, cur=1500),  # applied
        _row(app=1800, cur=1500, hw_thermal_slowdown=True),  # capped
    ]
    # Add distinct indices
    rows[0]["index"] = "0"; rows[1]["index"] = "1"
    with patch.object(cg, "_query_gpu", return_value=rows):
        s = cg.status()
    assert len(s["gpus"]) == 2
    assert s["any_capped"] is True

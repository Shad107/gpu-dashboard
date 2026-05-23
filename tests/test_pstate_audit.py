"""R&D #21.1 — P-state pinning advisor tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import pstate_audit as pa


# ── parse_pstate ───────────────────────────────────────────────────────


def test_parse_pstate_p0():
    assert pa.parse_pstate("P0") == 0


def test_parse_pstate_p12():
    assert pa.parse_pstate("P12") == 12


def test_parse_pstate_garbage():
    assert pa.parse_pstate("X0") is None
    assert pa.parse_pstate("") is None
    assert pa.parse_pstate("P") is None


def test_parse_pstate_with_whitespace():
    assert pa.parse_pstate("P3") == 3


# ── parse_int ──────────────────────────────────────────────────────────


def test_parse_int_normal():
    assert pa.parse_int("1500") == 1500


def test_parse_int_float_string():
    assert pa.parse_int("1500.0") == 1500


def test_parse_int_na():
    assert pa.parse_int("N/A") is None
    assert pa.parse_int("[N/A]") is None
    assert pa.parse_int("") is None


# ── is_clock_locked ────────────────────────────────────────────────────


def test_clock_locked_when_max_far_below_base():
    """If max < 90% of base → user locked clocks low."""
    assert pa.is_clock_locked(current_gr=1000, max_gr=1000, base_gr=1500) is True


def test_clock_not_locked_normal():
    assert pa.is_clock_locked(current_gr=1500, max_gr=1900, base_gr=1500) is False


def test_clock_not_locked_unknown():
    assert pa.is_clock_locked(None, None, None) is False


# ── classify ───────────────────────────────────────────────────────────


def test_classify_unknown_pstate():
    v = pa.classify(None, 80, 200, 350, False)
    assert v["verdict"] == "unknown"


def test_classify_ok_heavy_at_p0():
    v = pa.classify(pstate=0, util_pct=80, power_w=300,
                     power_limit_w=350, clock_locked=False)
    assert v["verdict"] == "ok"


def test_classify_silent_downshift_heavy_at_p3():
    """Heavy load (>50% util) but stuck at P3 → bug."""
    v = pa.classify(pstate=3, util_pct=80, power_w=200,
                     power_limit_w=350, clock_locked=False)
    assert v["verdict"] == "silent_downshift"
    assert "P3" in v["reason"]


def test_classify_silent_downshift_heavy_at_p2():
    """The R555 open-driver known-bug case."""
    v = pa.classify(pstate=2, util_pct=70, power_w=200,
                     power_limit_w=350, clock_locked=False)
    assert v["verdict"] == "silent_downshift"


def test_classify_power_save_idle():
    v = pa.classify(pstate=8, util_pct=0, power_w=20,
                     power_limit_w=350, clock_locked=False)
    assert v["verdict"] == "power_save_idle"


def test_classify_idle_at_low_pstate():
    """Idle but at P2 — not broken but not ideal."""
    v = pa.classify(pstate=2, util_pct=2, power_w=80,
                     power_limit_w=350, clock_locked=False)
    assert v["verdict"] == "ok"
    assert "Idle" in v["reason"]


def test_classify_mid_load():
    v = pa.classify(pstate=2, util_pct=25, power_w=150,
                     power_limit_w=350, clock_locked=False)
    assert v["verdict"] == "ok"
    assert "Mid load" in v["reason"]


def test_classify_clock_locked_overrides():
    """If user locked clocks, that takes precedence."""
    v = pa.classify(pstate=0, util_pct=80, power_w=300,
                     power_limit_w=350, clock_locked=True)
    assert v["verdict"] == "clock_locked"
    assert "reset-gpu-clocks" in v["reason"]


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_smi():
    with patch.object(pa, "_query_gpu", return_value=None):
        s = pa.status()
    assert s["ok"] is False
    assert s["gpus"] == []


def test_status_aggregates_downshift_count():
    fake = [
        {"index": "0", "name": "RTX 3090", "pstate": "P3",
         "utilization.gpu": "80", "clocks.current.graphics": "1200",
         "clocks.max.graphics": "1900", "clocks.gr": "1395",
         "power.draw": "200", "power.limit": "350"},
        {"index": "1", "name": "RTX 4090", "pstate": "P0",
         "utilization.gpu": "70", "clocks.current.graphics": "2500",
         "clocks.max.graphics": "2700", "clocks.gr": "2235",
         "power.draw": "400", "power.limit": "450"},
    ]
    with patch.object(pa, "_query_gpu", return_value=fake):
        s = pa.status()
    assert s["ok"] is True
    assert s["downshift_count"] == 1
    assert s["gpus"][0]["verdict"]["verdict"] == "silent_downshift"
    assert s["gpus"][1]["verdict"]["verdict"] == "ok"


def test_status_handles_idle_gpus():
    fake = [
        {"index": "0", "name": "RTX 3090", "pstate": "P8",
         "utilization.gpu": "0", "clocks.current.graphics": "210",
         "clocks.max.graphics": "1900", "clocks.gr": "1395",
         "power.draw": "30", "power.limit": "350"},
    ]
    with patch.object(pa, "_query_gpu", return_value=fake):
        s = pa.status()
    assert s["downshift_count"] == 0
    assert s["gpus"][0]["verdict"]["verdict"] == "power_save_idle"
